"""Файловый менеджер (только для администраторов).

Настройки модуля - data/modules/files.json:
    {"root": "/srv/data"}   - ограничить обзор указанным каталогом (по умолчанию доступна вся файловая система).
"""
import os
import shutil
import string
import tempfile

from scc.server import HTTPError, Response

MAX_EDIT = 2 * 1024 * 1024
MAX_LIST = 5000


def setup(api):
    win = api.is_windows

    def root():
        r = api.config.get("root")
        return os.path.realpath(r) if r else None

    def inside(p, base):
        return p == base or p.startswith(base.rstrip(os.sep) + os.sep)

    def resolve(path, allow_empty=False):
        base = root()
        if not path:
            if allow_empty:
                return base or ""
            raise HTTPError(400, "Не указан путь")
        p = os.path.realpath(os.path.expanduser(path))
        if base and not inside(p, base):
            raise HTTPError(403, "Путь вне разрешённого каталога")
        return p

    def drives():
        return [l + ":\\" for l in string.ascii_uppercase if os.path.exists(l + ":\\")]

    def crumbs(p):
        out, base = [], root()
        if win:
            out.append({"name": "Диски", "path": ""} if not base else {"name": base, "path": base})
            drive, rest = os.path.splitdrive(p)
            if drive:
                cur = drive + "\\"
                out.append({"name": drive + "\\", "path": cur})
                for part in [x for x in rest.split("\\") if x]:
                    cur = os.path.join(cur, part)
                    out.append({"name": part, "path": cur})
            return out
        out.append({"name": "/", "path": "/"})
        cur = ""
        for part in [x for x in p.split("/") if x]:
            cur += "/" + part
            out.append({"name": part, "path": cur})
        return out

    def info(path):
        try:
            st = os.lstat(path)
        except OSError:
            return None
        import stat as S
        kind = "dir" if S.S_ISDIR(st.st_mode) else "link" if S.S_ISLNK(st.st_mode) else "file"
        if kind == "link" and os.path.isdir(path):
            kind = "dir"
        return {"name": os.path.basename(path.rstrip("\\/")) or path, "type": kind, "size": st.st_size,
                "mtime": int(st.st_mtime), "mode": "%o" % (st.st_mode & 0o777)}

    def audit(req, what, p):
        api.log.info("files[%s]: %s %s", req.user["name"], what, p)

    def clean_name(name):
        name = str(name or "")
        if not name or name in (".", "..") or any(c in name for c in '/\\\0') or (win and any(c in name for c in '<>:"|?*')):
            raise HTTPError(400, "Недопустимое имя")
        return name

    # ------------------------------------------------------------ чтение
    @api.route("GET", "list")
    def list_dir(req):
        path = req.arg("path", "")
        if win and not path and not root():
            return {"path": "", "parts": [{"name": "Диски", "path": ""}], "entries": [
                {"name": d, "type": "dir", "size": 0, "mtime": 0, "mode": "", "path": d} for d in drives()],
                "sep": "\\", "parent": None, "root": None}
        p = resolve(path, allow_empty=True) if path else (root() or os.path.abspath(os.sep))
        if not os.path.isdir(p):
            raise HTTPError(404, "Каталог не найден")
        entries = []
        try:
            names = os.listdir(p)
        except PermissionError:
            raise HTTPError(403, "Нет доступа к каталогу")
        for n in names[:MAX_LIST]:
            i = info(os.path.join(p, n))
            if i:
                entries.append(i)
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        parent = os.path.dirname(p.rstrip("\\/")) if p.rstrip("\\/") else None
        base = root()
        if base and not inside(parent or "", base):
            parent = None
        if win and parent == p:
            parent = ""
        elif not win and parent == p:
            parent = None
        return {"path": p, "parts": crumbs(p), "entries": entries, "sep": os.sep, "parent": parent, "root": base,
                "truncated": len(names) > MAX_LIST}

    @api.route("GET", "read")
    def read(req):
        p = resolve(req.arg("path"))
        if not os.path.isfile(p):
            raise HTTPError(404, "Файл не найден")
        size = os.path.getsize(p)
        if size > MAX_EDIT:
            raise HTTPError(413, "Файл больше %d МБ - скачайте его" % (MAX_EDIT // 1048576))
        with open(p, "rb") as f:
            raw = f.read()
        if b"\0" in raw[:8192]:
            raise HTTPError(415, "Это бинарный файл - его можно только скачать")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPError(415, "Файл не в кодировке UTF-8 - редактирование отключено, чтобы не испортить его")
        return {"path": p, "content": text, "size": size, "mtime": int(os.path.getmtime(p)), "name": os.path.basename(p)}

    @api.route("GET", "download")
    def download(req):
        p = resolve(req.arg("path"))
        if not os.path.isfile(p):
            raise HTTPError(404, "Файл не найден")
        audit(req, "download", p)
        return Response.send_file(p, download_name=os.path.basename(p))

    # ------------------------------------------------------------ запись
    def atomic_write(dest, writer):
        d = os.path.dirname(dest)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".scc-")
        try:
            with os.fdopen(fd, "wb") as f:
                writer(f)
            if os.path.exists(dest):
                try:
                    shutil.copymode(dest, tmp)
                except OSError:
                    pass
            os.replace(tmp, dest)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @api.route("PUT", "write")
    def write(req):
        p = resolve(req.arg("path"))
        if os.path.isdir(p):
            raise HTTPError(400, "Это каталог")
        data = req.body(limit=MAX_EDIT * 2)
        try:
            atomic_write(p, lambda f: f.write(data))
        except OSError as e:
            raise HTTPError(403, "Не удалось записать файл: %s" % e.strerror)
        audit(req, "write", p)
        return {"ok": True, "size": len(data), "mtime": int(os.path.getmtime(p))}

    @api.route("PUT", "upload")
    def upload(req):
        d = resolve(req.arg("path"))
        if not os.path.isdir(d):
            raise HTTPError(404, "Каталог не найден")
        dest = os.path.join(d, clean_name(req.arg("name")))
        try:
            atomic_write(dest, lambda f: req.stream_to(f))
        except OSError as e:
            raise HTTPError(403, "Не удалось сохранить файл: %s" % e.strerror)
        audit(req, "upload", dest)
        return {"ok": True}

    @api.route("POST", "create")
    def create(req):
        d = req.json()
        base = resolve(d.get("path"))
        dest = os.path.join(base, clean_name(d.get("name")))
        kind = d.get("type", "file")
        if os.path.exists(dest):
            raise HTTPError(409, "Уже существует")
        try:
            if kind == "dir":
                os.mkdir(dest)
            else:
                open(dest, "x").close()
        except OSError as e:
            raise HTTPError(403, "Не удалось создать: %s" % e.strerror)
        audit(req, "create " + kind, dest)
        return {"ok": True}

    @api.route("POST", "rename")
    def rename(req):
        d = req.json()
        src = resolve(d.get("path"))
        dest = os.path.join(os.path.dirname(src), clean_name(d.get("name")))
        if os.path.exists(dest):
            raise HTTPError(409, "Файл с таким именем уже есть")
        try:
            os.rename(src, dest)
        except OSError as e:
            raise HTTPError(403, "Не удалось переименовать: %s" % e.strerror)
        audit(req, "rename %s ->" % src, dest)
        return {"ok": True}

    @api.route("POST", "delete")
    def delete(req):
        p = resolve(req.json().get("path"))
        if (root() and p == root()) or (win and p in drives()) or (not win and p == os.sep):
            raise HTTPError(400, "Корневой каталог удалять нельзя")
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        except OSError as e:
            raise HTTPError(403, "Не удалось удалить: %s" % e.strerror)
        audit(req, "delete", p)
        return {"ok": True}
