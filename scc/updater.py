"""Автообновление через GitHub Releases.

Берётся релиз, тег которого подходит под update.tag (точный тег или маска, например "v*"
либо "stable"), из него скачивается asset по маске update.asset, проверяется SHA-256
(если он указан в релизе), файлы заменяются, затем сервис перезапускается.
Сохраняются: config.json, data/ и пользовательские модули.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from .modules import find_root, safe_extract
from .util import read_json, vkey, write_json

MAX_DOWNLOAD = 300 * 1024 * 1024
KEEP_BACKUPS = 3
# Не трогаем при обновлении: настройки, данные и лаунчеры (run.sh/run.bat выполняются прямо сейчас -
# подмена файла скрипта "на лету" ломает работающий цикл перезапуска).
PRESERVE_TOP = {"config.json", "data", ".git", ".github", "__pycache__", "dist", "run.sh", "run.bat"}


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Не пересылать Authorization на другой хост (GitHub -> хранилище asset'ов)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if new is not None and urllib.request.urlparse(newurl).netloc != urllib.request.urlparse(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


_opener = urllib.request.build_opener(_SafeRedirect)


class Updater:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.record_file = os.path.join(app.data_dir, "update_state.json")
        self.record = read_json(self.record_file, {}) or {}
        self.state = {
            "current": app.version, "latest": None, "tag": None, "available": False,
            "checked": None, "error": None, "notes": "", "url": None,
            "busy": False, "phase": None, "done": 0, "total": 0, "verified": None,
        }
        self._release = None
        self._thread = None

    # ---------------- конфиг ----------------
    @property
    def cfg(self):
        return self.app.config.get("update", {})

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
        s["repo"] = self.cfg.get("repo")
        s["pattern"] = self.cfg.get("tag")
        s["enabled"] = bool(self.cfg.get("enabled"))
        s["installed_tag"] = self.record.get("tag")
        return s

    # ---------------- сеть ----------------
    def _request(self, url, accept="application/vnd.github+json", timeout=25):
        headers = {"User-Agent": "ServerControlWeb/" + self.app.version, "Accept": accept}
        tok = self.cfg.get("token")
        if tok:
            headers["Authorization"] = "Bearer " + tok
        return _opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)

    def _api_url(self, suffix):
        base = (self.cfg.get("api_url") or "https://api.github.com").rstrip("/")
        return "%s/repos/%s%s" % (base, self.cfg.get("repo"), suffix)

    # ---------------- проверка ----------------
    def check(self):
        with self.lock:
            self.state["error"] = None
        try:
            if not self.cfg.get("repo"):
                raise ValueError("Не указан репозиторий обновлений")
            with self._request(self._api_url("/releases?per_page=50")) as r:
                releases = json.loads(r.read().decode("utf-8"))
            best = self._pick(releases)
            with self.lock:
                self.state["checked"] = int(time.time())
                if not best:
                    self.state.update(latest=None, tag=None, available=False, notes="", url=None,
                                      error="Нет релизов с тегом, подходящим под «%s»" % self.cfg.get("tag"))
                    self._release = None
                    return self.snapshot()
                avail = self._is_newer(best)
                self._release = best
                self.state.update(
                    latest=best["tag_name"].lstrip("vV") if vkey(best["tag_name"]) else best["tag_name"],
                    tag=best["tag_name"], available=avail, notes=best.get("body") or "",
                    url=best.get("html_url"))
            if avail:
                self.app.emit("update_available", self.snapshot())
        except urllib.error.HTTPError as e:
            self._fail("GitHub API: HTTP %s" % e.code + (" (репозиторий не найден или приватный)" if e.code == 404 else ""))
        except Exception as e:  # noqa
            self._fail("Ошибка проверки: %s" % e)
        return self.snapshot()

    def _fail(self, msg):
        with self.lock:
            self.state["error"] = msg
            self.state["checked"] = int(time.time())

    def _pick(self, releases):
        pat = self.cfg.get("tag") or "v*"
        cands = []
        for r in releases if isinstance(releases, list) else []:
            if r.get("draft"):
                continue
            if r.get("prerelease") and not self.cfg.get("allow_prerelease"):
                continue
            if fnmatch.fnmatchcase(r.get("tag_name", ""), pat):
                cands.append(r)
        if not cands:
            return None
        if all(vkey(r["tag_name"]) for r in cands):
            return max(cands, key=lambda r: vkey(r["tag_name"]))
        return max(cands, key=lambda r: r.get("published_at") or "")

    def _asset(self, rel):
        pat = self.cfg.get("asset") or "*.zip"
        for a in rel.get("assets", []):
            if fnmatch.fnmatchcase(a.get("name", ""), pat) and a.get("name", "").endswith(".zip"):
                return a
        return None

    def _is_newer(self, rel):
        tag = rel["tag_name"]
        asset = self._asset(rel)
        stamp = (asset or {}).get("updated_at") or rel.get("published_at")
        k_new, k_cur = vkey(tag), vkey(self.app.version)
        rec = self.record
        if rec.get("tag") == tag:
            # «плавающий» тег: релиз пересобрали - изменился asset
            return bool(stamp) and rec.get("stamp") != stamp
        if k_new and k_cur:
            return k_new > k_cur
        return True  # нечисловые теги: любой другой тег считаем обновлением

    # ---------------- установка ----------------
    def apply_async(self):
        with self.lock:
            if self.state["busy"]:
                raise RuntimeError("Обновление уже выполняется")
            if not self._release:
                raise RuntimeError("Сначала проверьте наличие обновлений")
            self.state.update(busy=True, error=None, phase="download", done=0, total=0)
        self._thread = threading.Thread(target=self._apply, daemon=True)
        self._thread.start()

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _apply(self):
        work = tempfile.mkdtemp(prefix="scc-update-")
        try:
            rel = self._release
            asset = self._asset(rel)
            if asset:
                url = asset["url"] if self.cfg.get("token") else asset["browser_download_url"]
                accept = "application/octet-stream"
                expect = self._expected_sha(rel, asset)
                stamp = asset.get("updated_at")
            else:
                url, accept, expect = rel.get("zipball_url"), "application/zip", None
                stamp = rel.get("published_at")
                if not url:
                    raise RuntimeError("В релизе нет подходящего .zip (маска: %s)" % self.cfg.get("asset"))
            zpath = os.path.join(work, "release.zip")
            digest = self._download(url, accept, zpath)
            if self.cfg.get("require_checksum") and not expect:
                raise RuntimeError("В релизе нет SHA-256, а require_checksum включён")
            verified = None
            if expect:
                if digest != expect:
                    raise RuntimeError("Контрольная сумма не совпала - файл повреждён или подменён")
                verified = True
            self._set(phase="extract", verified=verified)
            stage = os.path.join(work, "stage")
            os.makedirs(stage)
            with zipfile.ZipFile(zpath) as zf:
                safe_extract(zf, stage)
            root = find_root(stage, "VERSION")
            if not root or not os.path.isfile(os.path.join(root, "scc", "__init__.py")) \
                    or not os.path.isfile(os.path.join(root, "run.py")):
                raise RuntimeError("Архив не похож на Server Control Web (нет VERSION, run.py или scc/)")
            self._set(phase="install")
            new_version = open(os.path.join(root, "VERSION"), encoding="utf-8").read().strip()
            self._install(root, new_version)
            self.record = {"tag": rel["tag_name"], "stamp": stamp, "version": new_version,
                           "installed": int(time.time())}
            write_json(self.record_file, self.record)
            self._set(phase="restart", busy=False)
            self.app.log.info("Обновлено до %s (%s), перезапуск", new_version, rel["tag_name"])
            self.app.request_restart()
        except Exception as e:  # noqa
            self.app.log.exception("Обновление не удалось")
            self._set(busy=False, phase=None, error="Обновление не удалось: %s" % e)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _expected_sha(self, rel, asset):
        d = asset.get("digest") or ""
        if d.startswith("sha256:"):
            return d[7:].lower()
        for a in rel.get("assets", []):
            if a.get("name") in (asset["name"] + ".sha256", asset["name"] + ".sha256sum"):
                try:
                    url = a["url"] if self.cfg.get("token") else a["browser_download_url"]
                    with self._request(url, "application/octet-stream") as r:
                        txt = r.read(4096).decode("utf-8", "replace").strip()
                    return txt.split()[0].lower()
                except Exception:
                    return None
        return None

    def _download(self, url, accept, dest):
        h = hashlib.sha256()
        with self._request(url, accept, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            self._set(total=total, done=0)
            if total > MAX_DOWNLOAD:
                raise RuntimeError("Файл слишком большой")
            done = 0
            with open(dest, "wb") as f:
                while True:
                    b = r.read(128 * 1024)
                    if not b:
                        break
                    done += len(b)
                    if done > MAX_DOWNLOAD:
                        raise RuntimeError("Файл слишком большой")
                    f.write(b)
                    h.update(b)
                    self._set(done=done)
        return h.hexdigest()

    # ---------------- замена файлов и откат ----------------
    def _backup_dir(self):
        return os.path.join(self.app.data_dir, "backup")

    def _install(self, root, new_version):
        app_root = str(self.app.root)
        bdir = os.path.join(self._backup_dir(), "%s-%d" % (self.app.version, int(time.time())))
        os.makedirs(bdir)
        items = [i for i in os.listdir(root) if i not in PRESERVE_TOP]
        # резервная копия того, что будет заменено
        for item in items:
            src = os.path.join(app_root, item)
            if item == "modules":
                for mid in os.listdir(os.path.join(root, "modules")):
                    ms = os.path.join(app_root, "modules", mid)
                    if os.path.isdir(ms):
                        shutil.copytree(ms, os.path.join(bdir, "modules", mid))
            elif os.path.isdir(src):
                shutil.copytree(src, os.path.join(bdir, item))
            elif os.path.isfile(src):
                shutil.copy2(src, os.path.join(bdir, item))
        try:
            self._copy_over(root, app_root, items)
        except Exception:
            self.app.log.error("Ошибка установки, откат из %s", bdir)
            self._copy_over(bdir, app_root, os.listdir(bdir))
            raise
        # ротация бэкапов
        bs = sorted(os.listdir(self._backup_dir()))
        for old in bs[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(self._backup_dir(), old), ignore_errors=True)

    @staticmethod
    def _copy_over(src_root, dst_root, items):
        for item in items:
            s, d = os.path.join(src_root, item), os.path.join(dst_root, item)
            if item == "modules" and os.path.isdir(s):
                for mid in os.listdir(s):
                    ms, md = os.path.join(s, mid), os.path.join(d, mid)
                    if os.path.isdir(md):
                        shutil.rmtree(md)
                    if os.path.isdir(ms):
                        os.makedirs(d, exist_ok=True)
                        shutil.copytree(ms, md)
            elif os.path.isdir(s):
                if os.path.isdir(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            elif os.path.isfile(s):
                shutil.copy2(s, d)

    def rollback(self):
        bd = self._backup_dir()
        if not os.path.isdir(bd) or not os.listdir(bd):
            raise RuntimeError("Резервных копий нет")
        last = sorted(os.listdir(bd))[-1]
        src = os.path.join(bd, last)
        self._copy_over(src, str(self.app.root), os.listdir(src))
        self.record = {}
        write_json(self.record_file, self.record)
        return last

    # ---------------- фоновая проверка ----------------
    def start_background(self):
        threading.Thread(target=self._loop, daemon=True, name="updater").start()

    def _loop(self):
        time.sleep(20)
        while True:
            try:
                if self.cfg.get("enabled") and self.app.configured:
                    self.check()
                    if self.state["available"] and self.cfg.get("auto_apply") and not self.state["busy"]:
                        self.apply_async()
            except Exception:
                self.app.log.exception("updater loop")
            hours = max(0.1, float(self.cfg.get("check_interval_hours") or 6))
            time.sleep(hours * 3600)
