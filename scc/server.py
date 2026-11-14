"""HTTP-сервер (stdlib): Request/Response, диспетчер, статика, TLS."""
import hashlib
import json
import mimetypes
import os
import re
import socket
import ssl
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import ROOT

WEB_DIR = os.path.join(str(ROOT), "scc", "web")
SESSION_COOKIE = "scc_session"
MAX_JSON = 10 * 1024 * 1024

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


class HTTPError(Exception):
    def __init__(self, status, message=""):
        Exception.__init__(self, message)
        self.status = status
        self.message = message


class Response:
    def __init__(self, body=b"", status=200, headers=None, ctype=None, stream=None, length=None, file=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.status = status
        self.headers = dict(headers or {})
        if ctype:
            self.headers["Content-Type"] = ctype
        self.stream = stream
        self.length = length
        self.file = file
        self.cookies = []

    @classmethod
    def json(cls, data, status=200, headers=None):
        return cls(json.dumps(data, ensure_ascii=False).encode("utf-8"), status, headers,
                   "application/json; charset=utf-8")

    @classmethod
    def text(cls, text, status=200, ctype="text/plain; charset=utf-8"):
        return cls(text, status, None, ctype)

    @classmethod
    def html(cls, text, status=200):
        return cls(text, status, None, "text/html; charset=utf-8")

    @classmethod
    def error(cls, status, message):
        return cls.json({"error": message}, status)

    @classmethod
    def send_file(cls, path, download_name=None, ctype=None):
        r = cls(file=path)
        r.headers["Content-Type"] = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if download_name:
            r.headers["Content-Disposition"] = _content_disposition(download_name)
        return r

    def set_cookie(self, name, value, max_age=None, http_only=True, same_site="Strict", path="/", secure=False):
        c = "%s=%s; Path=%s; SameSite=%s" % (name, value, path, same_site)
        if http_only:
            c += "; HttpOnly"
        if secure:
            c += "; Secure"
        if max_age is not None:
            c += "; Max-Age=%d" % max_age
        self.cookies.append(c)


def _content_disposition(name):
    from urllib.parse import quote
    safe = re.sub(r'[^\x20-\x7e]', '_', name).replace('"', "'")
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (safe, quote(name))


class Request:
    def __init__(self, handler, app):
        self.handler = handler
        self.app = app
        self.method = handler.command.upper()
        parts = urlsplit(handler.path)
        self.path = unquote(parts.path)
        self.raw_query = parts.query
        self.query = parse_qs(parts.query, keep_blank_values=True)
        self.headers = handler.headers
        self.ip = handler.client_address[0]
        if app.config.get("trust_proxy"):
            xff = handler.headers.get("X-Forwarded-For")
            if xff:
                self.ip = xff.split(",")[0].strip()
        self.cookies = {}
        try:
            c = SimpleCookie(handler.headers.get("Cookie", ""))
            self.cookies = {k: m.value for k, m in c.items()}
        except Exception:
            pass
        self.params = {}
        self.user = None
        self.route = None
        try:
            self.content_length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            raise HTTPError(400, "Некорректный Content-Length")
        self.consumed = 0
        self._body = None

    # --- аргументы запроса
    def arg(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

    @property
    def host(self):
        return self.headers.get("Host", "")

    # --- тело
    def read(self, n=-1):
        left = self.content_length - self.consumed
        if n < 0 or n > left:
            n = left
        if n <= 0:
            return b""
        data = self.handler.rfile.read(n)
        self.consumed += len(data)
        return data

    def body(self, limit=MAX_JSON):
        if self._body is None:
            if self.content_length > limit:
                raise HTTPError(413, "Слишком большое тело запроса")
            self._body = self.read()
        return self._body

    def json(self):
        b = self.body()
        if not b:
            return {}
        try:
            d = json.loads(b.decode("utf-8"))
        except ValueError:
            raise HTTPError(400, "Некорректный JSON")
        return d

    def stream_to(self, fp, chunk=256 * 1024):
        total = 0
        while True:
            b = self.read(chunk)
            if not b:
                break
            fp.write(b)
            total += len(b)
        return total

    def session_token(self):
        return self.cookies.get(SESSION_COOKIE)


def _etag(path):
    st = os.stat(path)
    return '"%x-%x"' % (int(st.st_mtime), st.st_size)


def safe_join(root, rel):
    root = os.path.realpath(root)
    p = os.path.realpath(os.path.join(root, rel))
    if p != root and not p.startswith(root + os.sep):
        return None
    return p


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ServerControlWeb"
    sys_version = ""
    timeout = 120

    def log_message(self, fmt, *args):  # тише по умолчанию
        pass

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = lambda self: self._serve()

    # ------------------------------------------------------------
    def _serve(self):
        app = self.server.app
        req = None
        try:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                raise HTTPError(411, "Chunked-запросы не поддерживаются")
            req = Request(self, app)
            resp = self._dispatch(app, req)
        except HTTPError as e:
            resp = Response.error(e.status, e.message or "Ошибка")
        except Exception:
            app.log.error("Необработанная ошибка: %s", traceback.format_exc())
            resp = Response.error(500, "Внутренняя ошибка сервера")
        try:
            self._send(app, req, resp)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True

    # ------------------------------------------------------------
    def _dispatch(self, app, req):
        # аутентификация
        auth = req.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            from .auth import AuthError
            try:
                req.user = app.auth.check_node_token(auth[7:].strip(), req.ip)
            except AuthError as e:
                raise HTTPError(e.status, str(e))
            if req.user is None:
                raise HTTPError(401, "Неверный токен")
        else:
            req.user = app.auth.session_for(req.session_token())

        # защита от CSRF: проверка Origin для небезопасных методов
        if req.method not in ("GET", "HEAD", "OPTIONS") and not (req.user and req.user.get("via") == "token"):
            origin = req.headers.get("Origin")
            if origin and urlsplit(origin).netloc != req.host:
                raise HTTPError(403, "Недопустимый Origin")

        # хук "request": модули могут перехватить любой запрос
        early = app.first("request", req)
        if early is not None:
            return self._coerce(early)

        route, params, other = app.match(req.method, req.path)
        if route is None:
            if req.method in ("GET", "HEAD"):
                r = self._static(app, req)
                if r is not None:
                    return r
            if other:
                raise HTTPError(405, "Метод не поддерживается")
            if req.path.startswith("/api/") or req.path.startswith("/node/"):
                raise HTTPError(404, "Не найдено")
            # SPA: отдаём index для неизвестных путей без точки
            if req.method == "GET" and "." not in os.path.basename(req.path):
                return self._index(app)
            raise HTTPError(404, "Не найдено")

        req.params, req.route = params, route
        if route.access != "public":
            if not req.user:
                raise HTTPError(401, "Требуется вход")
            if route.access == "admin" and req.user.get("role") != "admin":
                raise HTTPError(403, "Недостаточно прав")
            if (req.user.get("via") == "session" and req.method not in ("GET", "HEAD", "OPTIONS")
                    and req.headers.get("X-SCC-CSRF") != req.user.get("csrf")):
                raise HTTPError(403, "Неверный CSRF-токен")
        return self._coerce(route.handler(req))

    @staticmethod
    def _coerce(r):
        if isinstance(r, Response):
            return r
        if r is None:
            return Response.json({"ok": True})
        if isinstance(r, (dict, list)):
            return Response.json(r)
        if isinstance(r, tuple) and len(r) == 2:
            return Response.json(r[0], r[1])
        if isinstance(r, (bytes, str)):
            return Response.text(r)
        raise TypeError("Обработчик вернул неподдерживаемый тип: %r" % type(r))

    # ------------------------------------------------------------
    def _index(self, app):
        with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        head = "".join(h[1] for h in app.heads)
        head = app.filter("head_html", head)
        html = html.replace("<!--HEAD_INJECT-->", head).replace("{{v}}", app.version)
        html = app.filter("page_html", html)
        return self._nocache(Response.html(html))

    @staticmethod
    def _nocache(r):
        r.headers["Cache-Control"] = "no-store"
        return r

    def _static(self, app, req):
        p = req.path
        base = None
        if p.startswith("/static/"):
            base, rel = WEB_DIR, p[len("/static/"):]
        elif p == "/favicon.svg" or p == "/favicon.ico":
            base, rel = WEB_DIR, "favicon.svg"
        elif p.startswith("/m/"):
            # Публично только папка web/ модуля: /m/<id>/web/<файл> (main.py и прочее не отдаются).
            m = re.match(r"^/m/([a-z0-9_-]+)/web/(.+)$", p)
            if m:
                mod = app.modules.loaded.get(m.group(1))
                if mod:
                    base, rel = os.path.join(mod.dir, "web"), m.group(2)
        if base is None:
            return None
        full = safe_join(base, rel)
        if not full or not os.path.isfile(full):
            return None
        et = _etag(full)
        if req.headers.get("If-None-Match") == et:
            r = Response(b"", 304)
            r.headers["ETag"] = et
            return r
        r = Response.send_file(full)
        r.headers["ETag"] = et
        r.headers["Cache-Control"] = "no-cache"
        return r

    # ------------------------------------------------------------
    def _send(self, app, req, resp):
        head_only = self.command == "HEAD"
        resp = app.filter("response", resp, req) if req is not None else resp
        if not isinstance(resp, Response):
            resp = Response.error(500, "Модуль вернул некорректный ответ")
        status = resp.status
        hdr = dict(resp.headers)
        hdr.setdefault("X-Content-Type-Options", "nosniff")
        hdr.setdefault("X-Frame-Options", "DENY")
        hdr.setdefault("Referrer-Policy", "same-origin")
        hdr.setdefault("Content-Security-Policy",
                       "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                       "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if resp.status == 401 or (req is not None and req.path.startswith("/api/")):
            hdr.setdefault("Cache-Control", "no-store")

        fobj, start, end, size = None, 0, -1, None
        if resp.file is not None:
            try:
                size = os.path.getsize(resp.file)
                fobj = open(resp.file, "rb")
            except OSError:
                self.send_error_json(404, "Файл недоступен")
                return
            rng = self.headers.get("Range") if req is not None else None
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng or "")
            if m and status == 200 and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    fobj.close()
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
                hdr["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
                length = end - start + 1
            else:
                end = size - 1
                length = size
            hdr["Accept-Ranges"] = "bytes"
        elif resp.stream is not None:
            length = resp.length
        else:
            length = len(resp.body)

        self.send_response(status)
        for k, v in hdr.items():
            self.send_header(k, v)
        for c in resp.cookies:
            self.send_header("Set-Cookie", c)
        if length is not None:
            self.send_header("Content-Length", str(length))
        else:
            self.send_header("Connection", "close")
            self.close_connection = True

        # дочитать неиспользованное тело запроса, иначе keep-alive сломается
        if req is not None and req.consumed < req.content_length:
            left = req.content_length - req.consumed
            if left <= 1024 * 1024:
                try:
                    req.read(left)
                except Exception:
                    self.close_connection = True
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
        self.end_headers()
        if head_only or status in (204, 304):
            if fobj:
                fobj.close()
            return
        if fobj is not None:
            try:
                fobj.seek(start)
                left = end - start + 1
                while left > 0:
                    b = fobj.read(min(256 * 1024, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
            finally:
                fobj.close()
        elif resp.stream is not None:
            try:
                for chunk in resp.stream:
                    if chunk:
                        self.wfile.write(chunk)
            finally:
                close = getattr(resp.stream, "close", None)
                if close:
                    close()
        else:
            self.wfile.write(resp.body)

    def send_error_json(self, status, msg):
        b = json.dumps({"error": msg}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, app):
        self.app = app
        host = app.config.get("host") or "0.0.0.0"
        port = int(app.config.get("port") or 8080)
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        ThreadingHTTPServer.__init__(self, (host, port), Handler)
        tls = app.config.get("tls") or {}
        self.https = False
        if tls.get("cert") and tls.get("key"):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(tls["cert"], tls["key"])
            self.socket = ctx.wrap_socket(self.socket, server_side=True)
            self.https = True

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, socket.timeout, ssl.SSLError, BrokenPipeError)):
            return
        self.app.log.error("Ошибка соединения с %s: %s", client_address, exc)
