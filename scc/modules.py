"""Система модулей.

Модуль - папка modules/<id>/ с файлом module.json и (необязательно) main.py:

    def setup(api): ...      # вызывается при включении
    def teardown(api): ...   # вызывается при выключении (необязательно)

Модуль выполняется в том же процессе с теми же правами, что и панель, и получает
полный доступ: api.app (всё приложение), любые Python-модули, ОС. Через api можно
регистрировать маршруты (в т.ч. перекрывать системные), хуки, страницы UI,
подменять любые функции (api.patch) - всё автоматически откатывается при выключении.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import zipfile

from .util import PLATFORM, read_json, run_cmd, vkey, write_json

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
ALL_ROLES = ["panel", "node"]
ALL_PLATFORMS = ["windows", "linux", "darwin"]


def safe_extract(zf, dest, max_total=500 * 1024 * 1024):
    """Распаковка zip без выхода за пределы dest (zip-slip) и с лимитом размера."""
    dest = os.path.realpath(dest)
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if total > max_total:
            raise ValueError("Архив слишком большой")
        name = info.filename.replace("\\", "/")
        target = os.path.realpath(os.path.join(dest, name))
        if target != dest and not target.startswith(dest + os.sep):
            raise ValueError("Небезопасный путь в архиве: %s" % info.filename)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("Символические ссылки в архиве запрещены")
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def find_root(path, marker):
    """Найти каталог с файлом marker: в корне или внутри единственной вложенной папки."""
    if os.path.isfile(os.path.join(path, marker)):
        return path
    subs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)) and d != "__MACOSX"]
    if len(subs) == 1 and os.path.isfile(os.path.join(path, subs[0], marker)):
        return os.path.join(path, subs[0])
    return None


class ModuleAPI:
    """Объект, который получает setup(api) модуля."""

    def __init__(self, app, manifest, directory):
        self.app = app
        self.manifest = manifest
        self.id = manifest["id"]
        self.dir = directory
        self.log = app.log.getChild("mod." + self.id)
        self.data_dir = os.path.join(app.data_dir, "modules", self.id)
        os.makedirs(self.data_dir, exist_ok=True)
        self._cfg_path = os.path.join(app.data_dir, "modules", self.id + ".json")
        self._cfg = read_json(self._cfg_path, {}) or {}
        self.is_windows = PLATFORM == "windows"
        self.platform = PLATFORM
        self.run = run_cmd

    # ---- настройки модуля (data/modules/<id>.json)
    @property
    def config(self):
        return self._cfg

    def save_config(self):
        write_json(self._cfg_path, self._cfg, private=True)

    # ---- HTTP
    def route(self, method, path, handler=None, access="admin"):
        """Маршрут /api/m/<id>/<path>. Можно использовать как декоратор."""
        full = "/api/m/%s/%s" % (self.id, path.lstrip("/"))

        def reg(fn):
            self.app.add_route(method, full, fn, access=access, owner=self.id)
            return fn
        return reg(handler) if handler else reg

    def raw_route(self, method, path, handler=None, access="admin", prio=0):
        """Маршрут по любому пути. prio>0 перекрывает системные маршруты."""
        def reg(fn):
            self.app.add_route(method, path, fn, access=access, owner=self.id, prio=prio)
            return fn
        return reg(handler) if handler else reg

    # ---- события
    def hook(self, event, fn=None, prio=0):
        """События: request, response, boot, head_html, page_html, login, logout, setup_done,
        update_available, startup, shutdown. Можно использовать как декоратор."""
        def reg(f):
            self.app.hook(event, f, owner=self.id, prio=prio)
            return f
        return reg(fn) if fn else reg

    # ---- интерфейс
    def add_nav(self, id, title, icon="package", page=None, order=100, badge=None):
        self.app.nav.append({"id": id, "title": title, "icon": icon, "page": page or id,
                             "order": order, "badge": badge, "owner": self.id})

    def add_script(self, url):
        self.app.assets.append((self.id, "script", url))

    def add_style(self, url):
        self.app.assets.append((self.id, "style", url))

    def inject_head(self, html):
        """Вставить произвольный HTML в <head> главной страницы."""
        self.app.heads.append((self.id, html))

    # ---- подмена чего угодно
    def patch(self, obj, name, replacement):
        """Заменить атрибут obj.name; оригинал восстановится при выключении модуля.
        Возвращает оригинал: api.patch(app.auth, 'verify', lambda n, p: orig(n, p) or ...)"""
        original = getattr(obj, name)
        setattr(obj, name, replacement)

        def restore():
            setattr(obj, name, original)
        self.app.restorers.append((self.id, restore))
        return original

    def on_unload(self, fn):
        self.app.restorers.append((self.id, fn))


class Loaded:
    def __init__(self, manifest, directory, module, api):
        self.manifest, self.dir, self.module, self.api = manifest, directory, module, api

    @property
    def id(self):
        return self.manifest["id"]


class ModuleManager:
    def __init__(self, app):
        self.app = app
        self.dir = os.path.join(str(app.root), "modules")
        os.makedirs(self.dir, exist_ok=True)
        self.state_file = os.path.join(app.data_dir, "modules.json")
        self.state = read_json(self.state_file, {}) or {}
        self.loaded = {}
        self.errors = {}
        self.lock = threading.RLock()

    # ---------- каталог ----------
    def manifest(self, mid):
        d = os.path.join(self.dir, mid)
        m = read_json(os.path.join(d, "module.json"))
        if not isinstance(m, dict):
            return None
        m.setdefault("id", mid)
        m.setdefault("name", mid)
        m.setdefault("version", "0.0.0")
        m.setdefault("roles", ALL_ROLES)
        m.setdefault("platforms", ALL_PLATFORMS)
        m.setdefault("order", 100)
        return m

    def discover(self):
        out = {}
        for name in sorted(os.listdir(self.dir)):
            if not ID_RE.match(name) or not os.path.isdir(os.path.join(self.dir, name)):
                continue
            m = self.manifest(name)
            if m and m["id"] == name:
                out[name] = m
        return out

    def compatibility(self, m):
        if self.app.type not in m["roles"]:
            return "Работает только на типе: %s" % ", ".join(m["roles"])
        if PLATFORM not in m["platforms"]:
            return "Не поддерживается на %s" % PLATFORM
        mc = m.get("min_core")
        if mc and vkey(mc) and vkey(self.app.version) and vkey(self.app.version) < vkey(mc):
            return "Нужна версия ядра %s или новее" % mc
        return None

    def is_enabled(self, m):
        en = self.state.get("enabled", {})
        if m["id"] in en:
            return bool(en[m["id"]])
        return bool(m.get("default_enabled", False))

    def _save_state(self):
        write_json(self.state_file, self.state)

    def listing(self):
        res = []
        for mid, m in self.discover().items():
            reason = self.compatibility(m)
            res.append({
                "id": mid, "name": m["name"], "version": m["version"],
                "description": m.get("description", ""), "author": m.get("author", ""),
                "icon": m.get("icon", "package"),
                "builtin": bool(m.get("builtin")), "roles": m["roles"], "platforms": m["platforms"],
                "enabled": self.is_enabled(m), "loaded": mid in self.loaded,
                "compatible": reason is None, "reason": reason, "error": self.errors.get(mid),
                "requires": m.get("requires", []),
            })
        return res

    # ---------- загрузка ----------
    def load_all(self):
        mods = self.discover()
        order = sorted(mods.values(), key=lambda m: (m["order"], m["id"]))
        for m in order:
            if self.is_enabled(m):
                self.load(m["id"])

    def load(self, mid, _stack=()):
        with self.lock:
            if mid in self.loaded:
                return True
            m = self.manifest(mid)
            if not m:
                self.errors[mid] = "module.json не найден или повреждён"
                return False
            reason = self.compatibility(m)
            if reason:
                self.errors[mid] = reason
                return False
            if mid in _stack:
                self.errors[mid] = "Циклическая зависимость"
                return False
            for dep in m.get("requires", []):
                if not self.load(dep, _stack + (mid,)):
                    self.errors[mid] = "Не удалось загрузить зависимость: %s" % dep
                    return False
            d = os.path.join(self.dir, mid)
            api = ModuleAPI(self.app, m, d)
            mod = None
            try:
                main = os.path.join(d, m.get("entry", "main.py"))
                if os.path.isfile(main):
                    name = "scc_mod_" + mid.replace("-", "_")
                    spec = importlib.util.spec_from_file_location(name, main, submodule_search_locations=[d])
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[name] = mod
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "setup"):
                        mod.setup(api)
            except Exception as e:
                self.app.log.exception("Модуль %s не загружен", mid)
                self.errors[mid] = "%s: %s" % (type(e).__name__, e)
                self.app.unregister_owner(mid)
                self._forget_imports(mid)
                return False
            self.loaded[mid] = Loaded(m, d, mod, api)
            self.errors.pop(mid, None)
            self.app.log.info("Модуль загружен: %s %s", mid, m["version"])
            return True

    def _forget_imports(self, mid):
        prefix = "scc_mod_" + mid.replace("-", "_")
        for k in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
            del sys.modules[k]

    def unload(self, mid):
        with self.lock:
            # сначала выгружаем зависимые
            for other in list(self.loaded.values()):
                if mid in other.manifest.get("requires", []):
                    self.unload(other.id)
            ld = self.loaded.pop(mid, None)
            if not ld:
                return
            try:
                if ld.module is not None and hasattr(ld.module, "teardown"):
                    ld.module.teardown(ld.api)
            except Exception:
                self.app.log.exception("teardown %s", mid)
            self.app.unregister_owner(mid)
            self._forget_imports(mid)
            self.app.log.info("Модуль выгружен: %s", mid)

    def set_enabled(self, mid, enabled):
        m = self.manifest(mid)
        if not m:
            raise KeyError(mid)
        self.state.setdefault("enabled", {})[mid] = bool(enabled)
        self._save_state()
        if enabled:
            ok = self.load(mid)
            return ok, self.errors.get(mid)
        self.unload(mid)
        return True, None

    # ---------- установка / удаление ----------
    def install_zip(self, zip_path):
        tmp = tempfile.mkdtemp(prefix="scc-mod-")
        try:
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    safe_extract(zf, tmp, 100 * 1024 * 1024)
            except zipfile.BadZipFile:
                raise ValueError("Это не zip-архив")
            root = find_root(tmp, "module.json")
            if not root:
                raise ValueError("В архиве нет module.json")
            m = read_json(os.path.join(root, "module.json"))
            if not isinstance(m, dict) or not ID_RE.match(str(m.get("id", ""))):
                raise ValueError("Некорректный module.json: нужен id вида [a-z0-9_-]")
            mid = m["id"]
            old = self.manifest(mid)
            if old and old.get("builtin"):
                raise ValueError("Нельзя перезаписать встроенный модуль «%s»" % mid)
            was_loaded = mid in self.loaded
            self.unload(mid)
            dest = os.path.join(self.dir, mid)
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            shutil.copytree(root, dest)
            # установленный модуль выключен, пока администратор его явно не включит
            self.state.setdefault("enabled", {}).setdefault(mid, False)
            self._save_state()
            if was_loaded:
                self.set_enabled(mid, True)
            return mid
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def uninstall(self, mid):
        m = self.manifest(mid)
        if not m:
            raise KeyError(mid)
        if m.get("builtin"):
            raise ValueError("Встроенный модуль нельзя удалить, его можно только выключить")
        self.unload(mid)
        shutil.rmtree(os.path.join(self.dir, mid), ignore_errors=True)
        self.state.get("enabled", {}).pop(mid, None)
        self._save_state()
        self.errors.pop(mid, None)

    # ---------- данные для UI ----------
    def nav_items(self):
        items = []
        for ld in self.loaded.values():
            nav = ld.manifest.get("nav")
            if not nav:
                continue
            for n in (nav if isinstance(nav, list) else [nav]):
                it = dict(n)
                it.setdefault("id", ld.id)
                it.setdefault("page", it["id"])
                it.setdefault("icon", ld.manifest.get("icon", "package"))
                it.setdefault("order", ld.manifest.get("order", 100))
                it["module"] = ld.id
                items.append(it)
        for n in self.app.nav:
            it = {k: v for k, v in n.items() if k != "owner"}
            it["module"] = n.get("owner")
            items.append(it)
        items.sort(key=lambda x: (x.get("order", 100), x["id"]))
        return items

    def ui_assets(self):
        res = []
        for ld in sorted(self.loaded.values(), key=lambda l: (l.manifest["order"], l.id)):
            v = ld.manifest["version"]
            css = ld.manifest.get("css") or []
            if isinstance(css, str):
                css = [css]
            ui = ld.manifest.get("ui")
            res.append({
                "id": ld.id, "name": ld.manifest["name"], "version": v,
                "css": ["/m/%s/%s?v=%s" % (ld.id, c, v) for c in css],
                "ui": ("/m/%s/%s?v=%s" % (ld.id, ui, v)) if ui else None,
            })
        extra = [{"kind": k, "url": u} for _, k, u in self.app.assets]
        return res, extra
