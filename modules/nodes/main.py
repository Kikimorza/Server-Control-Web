"""Ноды: список удалённых серверов и прозрачный прокси /node/<id>/<любой путь API>.

Панель подставляет токен ноды (Authorization: Bearer ...), поэтому любой модуль, установленный
на ноде, автоматически работает из панели - без отдельного кода для удалённого режима.
Прокси доступен только администраторам панели.
"""
import http.client
import json
import re
import secrets
import ssl
import threading
import time
from urllib.parse import quote, urlsplit

from scc.server import HTTPError, Response

COPY_HEADERS = ("Content-Type", "Content-Disposition", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified")
FWD_HEADERS = ("Content-Type", "Accept", "Range", "If-None-Match")


def _connect(node, timeout):
    u = urlsplit(node["url"])
    if u.scheme == "https":
        ctx = ssl._create_unverified_context() if node.get("insecure") else ssl.create_default_context()
        return http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)


def _base(node):
    return urlsplit(node["url"]).path.rstrip("/")


def _fetch_json(node, path, timeout=6):
    """GET небольшого JSON с ноды. Возвращает (status, data|None)."""
    conn = _connect(node, timeout)
    try:
        conn.request("GET", _base(node) + path, headers={"Authorization": "Bearer " + node["token"], "Accept": "application/json"})
        r = conn.getresponse()
        raw = r.read(1024 * 1024)
        try:
            return r.status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return r.status, None
    finally:
        conn.close()


def _normalize_url(url):
    url = str(url or "").strip().rstrip("/")
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPError(400, "Адрес ноды должен начинаться с http:// или https://")
    if u.username or u.password:
        raise HTTPError(400, "Не указывайте логин и пароль в адресе")
    return url


def setup(api):
    cfg = api.config
    cfg.setdefault("nodes", [])
    lock = threading.RLock()

    def find(nid):
        for n in cfg["nodes"]:
            if n["id"] == nid:
                return n
        return None

    def public(n):
        return {"id": n["id"], "name": n["name"], "url": n["url"], "insecure": bool(n.get("insecure")),
                "added": n.get("added")}

    def probe(node):
        """Проверка связи: информация об агенте + время отклика."""
        t0 = time.time()
        try:
            st, data = _fetch_json(node, "/api/agent/info")
        except Exception as e:  # noqa
            return {"online": False, "error": "%s" % (getattr(e, "reason", None) or e)}
        ms = int((time.time() - t0) * 1000)
        if st in (401, 403):
            return {"online": False, "error": "Нода отклонила токен (HTTP %d)" % st}
        if st != 200 or not isinstance(data, dict):
            return {"online": False, "error": "Неожиданный ответ ноды (HTTP %d)" % st}
        return {"online": True, "ms": ms, "info": data}

    # ---------------------------------------------------------- boot: список нод для шапки
    @api.hook("boot")
    def on_boot(data, req):
        u = req.user
        if data.get("authenticated") and u and u.get("role") == "admin":
            data["nodes"] = [{"id": n["id"], "name": n["name"]} for n in cfg["nodes"]]
        return data

    # ---------------------------------------------------------- API
    @api.route("GET", "list")
    def list_nodes(req):
        return {"nodes": [public(n) for n in cfg["nodes"]]}

    @api.route("GET", "status")
    def status(req):
        nodes, res = list(cfg["nodes"]), {}

        def work(n):
            res[n["id"]] = probe(n)
        ths = [threading.Thread(target=work, args=(n,), daemon=True) for n in nodes]
        for t in ths:
            t.start()
        for t in ths:
            t.join(10)
        return {"nodes": [dict(public(n), **res.get(n["id"], {"online": False, "error": "нет ответа"})) for n in nodes]}

    @api.route("POST", "add")
    def add(req):
        d = req.json()
        name = str(d.get("name") or "").strip()[:64]
        token = str(d.get("token") or "").strip()
        if not name:
            raise HTTPError(400, "Укажите название ноды")
        if not token:
            raise HTTPError(400, "Укажите токен доступа ноды")
        node = {"id": "", "name": name, "url": _normalize_url(d.get("url")), "token": token,
                "insecure": bool(d.get("insecure")), "added": int(time.time())}
        r = probe(node)
        if not r["online"]:
            raise HTTPError(400, "Не удалось подключиться: %s" % r.get("error"))
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "node"
        with lock:
            node["id"] = "%s-%s" % (slug, secrets.token_hex(2))
            cfg["nodes"].append(node)
            api.save_config()
        api.log.info("Добавлена нода %s (%s)", name, node["url"])
        return {"ok": True, "node": public(node), "info": r.get("info")}

    @api.route("POST", "<id>/update")
    def update(req):
        n = find(req.params["id"])
        if not n:
            raise HTTPError(404, "Нода не найдена")
        d = req.json()
        new = dict(n)
        if "name" in d and str(d["name"]).strip():
            new["name"] = str(d["name"]).strip()[:64]
        if "url" in d:
            new["url"] = _normalize_url(d["url"])
        if d.get("token"):
            new["token"] = str(d["token"]).strip()
        if "insecure" in d:
            new["insecure"] = bool(d["insecure"])
        r = probe(new)
        if not r["online"]:
            raise HTTPError(400, "Не удалось подключиться: %s" % r.get("error"))
        with lock:
            n.update(new)
            api.save_config()
        return {"ok": True}

    @api.route("DELETE", "<id>")
    def delete(req):
        with lock:
            n = find(req.params["id"])
            if not n:
                raise HTTPError(404, "Нода не найдена")
            cfg["nodes"].remove(n)
            api.save_config()
        return {"ok": True}

    # ---------------------------------------------------------- прокси
    @api.raw_route("*", "/node/<id>/<path:path>", access="admin")
    def proxy(req):
        node = find(req.params["id"])
        if not node:
            raise HTTPError(404, "Нода не найдена")
        url = _base(node) + "/" + quote(req.params["path"], safe="/@:+,;=!$&'()*~-._")
        if req.raw_query:
            url += "?" + req.raw_query
        conn = _connect(node, 620)
        try:
            conn.putrequest(req.method, url, skip_accept_encoding=True)
            for h in FWD_HEADERS:
                v = req.headers.get(h)
                if v:
                    conn.putheader(h, v)
            conn.putheader("Authorization", "Bearer " + node["token"])
            if req.method in ("POST", "PUT", "PATCH") or req.content_length:
                conn.putheader("Content-Length", str(req.content_length))
            conn.endheaders()
            while True:
                chunk = req.read(65536)
                if not chunk:
                    break
                conn.send(chunk)
            up = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise HTTPError(502, "Нода «%s» недоступна: %s" % (node["name"], getattr(e, "reason", None) or e))
        if up.status in (401, 403) and up.getheader("Content-Type", "").startswith("application/json"):
            # не отдаём 401 браузеру: интерфейс решит, что завершился вход в панель
            msg = "Нода отклонила запрос (HTTP %d)" % up.status
            try:
                msg += ": " + json.loads(up.read(65536).decode("utf-8")).get("error", "")
            except Exception:  # noqa
                pass
            conn.close()
            raise HTTPError(502 if up.status == 401 else 403, msg)
        hdrs = {h: up.getheader(h) for h in COPY_HEADERS if up.getheader(h)}
        if req.method == "HEAD" or up.status in (204, 304):
            conn.close()
            return Response(b"", up.status, hdrs)
        length = up.getheader("Content-Length")

        def body():
            try:
                while True:
                    b = up.read(65536)
                    if not b:
                        break
                    yield b
            finally:
                conn.close()
        return Response(stream=body(), status=up.status, headers=hdrs, length=int(length) if length else None)
