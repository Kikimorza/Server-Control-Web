"""Системные маршруты: boot, мастер настройки, вход, настройки, модули, обновления."""
import hmac
import os
import platform
import re
import shutil
import socket
import tempfile
import time
import urllib.request

from .auth import AuthError
from .core import TYPES, TYPE_NODE, TYPE_PANEL
from .server import HTTPError, Response, SESSION_COOKIE


def _auth_call(fn, *a):
    try:
        return fn(*a)
    except AuthError as e:
        raise HTTPError(e.status, str(e))


def register(app):
    R = app.add_route

    # ------------------------------------------------------------ boot
    def boot(req):
        cfg = app.config
        data = {
            "version": app.version, "configured": app.configured, "type": cfg.get("type"),
            "types": TYPES, "name": cfg.get("name") or "", "authenticated": False,
        }
        if not app.configured:
            data["setup"] = {"token_required": True, "type_preset": cfg.get("type") in TYPES,
                             "hostname": socket.gethostname(), "port": cfg.get("port")}
            return app.filter("boot", data, req)
        u = req.user
        if u and u.get("via") == "session":
            ui, extra = app.modules.ui_assets()
            data.update({
                "authenticated": True,
                "user": {"name": u["name"], "role": u["role"]},
                "csrf": u["csrf"],
                "type_label": TYPES[cfg["type"]],
                "hostname": socket.gethostname(),
                "nav": app.modules.nav_items(),
                "modules": ui, "assets": extra,
                "update": {k: v for k, v in app.updater.snapshot().items() if k in
                           ("available", "latest", "tag", "current", "busy")},
            })
        return app.filter("boot", data, req)
    R("GET", "/api/boot", boot, access="public")

    # ------------------------------------------------------------ setup
    def setup(req):
        if app.configured:
            raise HTTPError(409, "Первоначальная настройка уже выполнена")
        d = req.json()
        tok = app.ensure_setup_token()
        if not hmac.compare_digest(str(d.get("setup_token", "")).strip(), tok):
            _fail = app.auth
            try:
                with app.auth.lock:
                    app.auth._check_lock(req.ip)
                    app.auth._fail(req.ip)
            except AuthError as e:
                raise HTTPError(e.status, str(e))
            raise HTTPError(403, "Неверный код установки. Он выведен в консоли сервера и записан в data/setup_token.txt")
        typ = d.get("type") or app.config.get("type")
        if typ not in TYPES:
            raise HTTPError(400, "Выберите тип установки")
        name = str(d.get("name") or "").strip()[:64] or socket.gethostname()
        if app.auth.has_users():
            raise HTTPError(409, "Учётная запись уже создана")
        _auth_call(app.auth.create_user, str(d.get("username", "")).strip(), d.get("password"), "admin")
        up = d.get("update") or {}
        ucfg = app.config["update"]
        if isinstance(up, dict):
            for k in ("enabled", "auto_apply", "allow_prerelease"):
                if k in up:
                    ucfg[k] = bool(up[k])
            for k in ("tag", "repo", "asset"):
                if isinstance(up.get(k), str) and up[k].strip():
                    ucfg[k] = up[k].strip()
        node_token = app.auth.new_node_token() if typ == TYPE_NODE or d.get("create_node_token") else None
        app.config.update(type=typ, name=name, setup_done=True)
        app.save_config()
        app.clear_setup_token()
        # загрузить модули под выбранный тип
        app.modules.load_all()
        token = app.auth.login(str(d["username"]).strip(), d["password"], req.ip)
        app.emit("setup_done", typ)
        resp = Response.json({"ok": True, "node_token": node_token})
        resp.set_cookie(SESSION_COOKIE, token, max_age=int(app.config["session_hours"] * 3600),
                        secure=bool(req.headers.get("X-Forwarded-Proto") == "https"))
        return resp
    R("POST", "/api/setup", setup, access="public")

    # ------------------------------------------------------------ login/logout
    def login(req):
        if not app.configured:
            raise HTTPError(409, "Сначала выполните первоначальную настройку")
        d = req.json()
        token = _auth_call(app.auth.login, str(d.get("username", "")), str(d.get("password", "")), req.ip)
        app.emit("login", d.get("username"), req.ip)
        resp = Response.json({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, max_age=int(float(app.config["session_hours"]) * 3600),
                        secure=bool(req.headers.get("X-Forwarded-Proto") == "https"))
        return resp
    R("POST", "/api/login", login, access="public")

    def logout(req):
        app.auth.logout(req.session_token())
        app.emit("logout", req.user["name"] if req.user else None)
        resp = Response.json({"ok": True})
        resp.set_cookie(SESSION_COOKIE, "", max_age=0)
        return resp
    R("POST", "/api/logout", logout, access="public")

    # ------------------------------------------------------------ agent
    def agent_info(req):
        return {"name": app.config.get("name"), "type": app.type, "version": app.version,
                "hostname": socket.gethostname(), "os": platform.platform(), "uptime": int(app.uptime()),
                "modules": sorted(app.modules.loaded)}
    R("GET", "/api/agent/info", agent_info, access="user")

    # ------------------------------------------------------------ account
    def change_password(req):
        d = req.json()
        u = req.user
        if u.get("via") != "session":
            raise HTTPError(400, "Недоступно для токена ноды")
        if not app.auth.verify(u["name"], d.get("old", "")):
            raise HTTPError(403, "Текущий пароль неверен")
        _auth_call(app.auth.set_password, u["name"], d.get("new"))
        token = app.auth.login(u["name"], d["new"], req.ip)
        resp = Response.json({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, max_age=int(float(app.config["session_hours"]) * 3600))
        return resp
    R("POST", "/api/account/password", change_password, access="user")

    # ------------------------------------------------------------ users (admin)
    R("GET", "/api/users", lambda req: {"users": app.auth.list_users()})

    def add_user(req):
        d = req.json()
        _auth_call(app.auth.create_user, str(d.get("username", "")).strip(), d.get("password"), d.get("role", "viewer"))
        return {"ok": True}
    R("POST", "/api/users", add_user)

    def del_user(req):
        if req.user.get("name") == req.params["name"]:
            raise HTTPError(400, "Нельзя удалить самого себя")
        _auth_call(app.auth.delete_user, req.params["name"])
        return {"ok": True}
    R("DELETE", "/api/users/<name>", del_user)

    def reset_pw(req):
        _auth_call(app.auth.set_password, req.params["name"], req.json().get("password"))
        return {"ok": True}
    R("POST", "/api/users/<name>/password", reset_pw)

    def set_role(req):
        if req.user.get("name") == req.params["name"]:
            raise HTTPError(400, "Нельзя изменить собственную роль")
        _auth_call(app.auth.set_role, req.params["name"], req.json().get("role"))
        return {"ok": True}
    R("POST", "/api/users/<name>/role", set_role)

    # ------------------------------------------------------------ settings
    def get_settings(req):
        c = app.config
        return {
            "name": c.get("name"), "type": c.get("type"), "types": TYPES,
            "host": c.get("host"), "port": c.get("port"),
            "tls": bool((c.get("tls") or {}).get("cert")),
            "session_hours": c.get("session_hours"), "trust_proxy": c.get("trust_proxy"),
            "update": {k: v for k, v in c["update"].items() if k != "token"},
            "update_token_set": bool(c["update"].get("token")),
            "node_token_set": bool(c.get("node", {}).get("token_hash")),
            "config_path": app.config_path, "data_dir": app.data_dir,
        }
    R("GET", "/api/settings", get_settings)

    def save_settings(req):
        d = req.json()
        c = app.config
        needs_restart = False
        if "name" in d:
            c["name"] = str(d["name"]).strip()[:64]
        if d.get("type") in TYPES and d["type"] != c.get("type"):
            c["type"] = d["type"]
            needs_restart = True
        if "host" in d and str(d["host"]).strip() and d["host"] != c.get("host"):
            c["host"] = str(d["host"]).strip()
            needs_restart = True
        if "port" in d:
            try:
                p = int(d["port"])
                if not 1 <= p <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPError(400, "Порт должен быть числом 1-65535")
            if p != c.get("port"):
                c["port"] = p
                needs_restart = True
        if "session_hours" in d:
            try:
                c["session_hours"] = max(0.5, min(24 * 90, float(d["session_hours"])))
            except (TypeError, ValueError):
                raise HTTPError(400, "Неверная длительность сессии")
        if "trust_proxy" in d:
            c["trust_proxy"] = bool(d["trust_proxy"])
        up = d.get("update")
        if isinstance(up, dict):
            u = c["update"]
            for k in ("enabled", "auto_apply", "allow_prerelease", "require_checksum"):
                if k in up:
                    u[k] = bool(up[k])
            for k in ("repo", "tag", "asset", "api_url"):
                if k in up and isinstance(up[k], str) and up[k].strip():
                    u[k] = up[k].strip()
            if "check_interval_hours" in up:
                try:
                    u["check_interval_hours"] = max(0.1, float(up["check_interval_hours"]))
                except (TypeError, ValueError):
                    raise HTTPError(400, "Неверный интервал проверки")
            if isinstance(up.get("token"), str):
                u["token"] = up["token"].strip()
        app.save_config()
        app.emit("config_saved")
        return {"ok": True, "restart_required": needs_restart}
    R("POST", "/api/settings", save_settings)

    def rotate_node_token(req):
        return {"token": app.auth.new_node_token()}
    R("POST", "/api/settings/node-token", rotate_node_token)

    def restart(req):
        app.log.info("Перезапуск по запросу %s", req.user["name"])
        app.request_restart()
        return {"ok": True}
    R("POST", "/api/system/restart", restart)

    # ------------------------------------------------------------ modules
    R("GET", "/api/modules", lambda req: {"modules": app.modules.listing()}, access="user")

    def mod_toggle(req):
        mid, on = req.params["id"], req.params["action"] == "enable"
        try:
            ok, err = app.modules.set_enabled(mid, on)
        except KeyError:
            raise HTTPError(404, "Модуль не найден")
        return {"ok": ok, "error": err}
    R("POST", "/api/modules/<id>/<action>", mod_toggle)

    def mod_delete(req):
        try:
            app.modules.uninstall(req.params["id"])
        except KeyError:
            raise HTTPError(404, "Модуль не найден")
        except ValueError as e:
            raise HTTPError(400, str(e))
        return {"ok": True}
    R("DELETE", "/api/modules/<id>", mod_delete)

    def mod_install(req):
        tmp = tempfile.mkdtemp(prefix="scc-up-")
        zp = os.path.join(tmp, "m.zip")
        try:
            url = req.arg("url")
            if url:
                if not re.match(r"^https?://", url):
                    raise HTTPError(400, "Нужна ссылка http(s)")
                rq = urllib.request.Request(url, headers={"User-Agent": "ServerControlWeb/" + app.version})
                with urllib.request.urlopen(rq, timeout=60) as r, open(zp, "wb") as f:
                    left = 50 * 1024 * 1024
                    while True:
                        b = r.read(65536)
                        if not b:
                            break
                        left -= len(b)
                        if left < 0:
                            raise HTTPError(413, "Файл больше 50 МБ")
                        f.write(b)
            else:
                if req.content_length > 50 * 1024 * 1024:
                    raise HTTPError(413, "Файл больше 50 МБ")
                with open(zp, "wb") as f:
                    req.stream_to(f)
            try:
                mid = app.modules.install_zip(zp)
            except ValueError as e:
                raise HTTPError(400, str(e))
            return {"ok": True, "id": mid}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    R("POST", "/api/modules/install", mod_install)

    # ------------------------------------------------------------ updates
    R("GET", "/api/update/status", lambda req: app.updater.snapshot())
    R("POST", "/api/update/check", lambda req: app.updater.check())

    def upd_apply(req):
        try:
            app.updater.apply_async()
        except RuntimeError as e:
            raise HTTPError(409, str(e))
        return {"ok": True}
    R("POST", "/api/update/apply", upd_apply)
