"""Ядро: конфигурация, реестр маршрутов, хуки, навигация."""
import logging
import logging.handlers
import os
import re
import secrets
import threading
import time

from . import ROOT, __version__
from .util import deep_merge, read_json, write_json

TYPE_PANEL = "panel"
TYPE_NODE = "node"
TYPES = {
    TYPE_PANEL: "Хостинг и нода управления",
    TYPE_NODE: "Нода",
}

DEFAULT_CONFIG = {
    # Тип установки: "panel" (хостинг + нода управления) или "node" (просто нода).
    # Если не указан здесь - будет выбран в мастере первой настройки.
    "type": None,
    "name": "",
    "host": "0.0.0.0",
    "port": 8080,
    "tls": {"cert": "", "key": ""},
    "session_hours": 12,
    "trust_proxy": False,
    "node": {"token_hash": ""},
    "update": {
        "enabled": True,
        "repo": "Kikimorza/Server-Control-Web",
        "tag": "v*",
        "asset": "server-control-web*.zip",
        "check_interval_hours": 6,
        "auto_apply": False,
        "allow_prerelease": False,
        "require_checksum": False,
        "api_url": "https://api.github.com",
        "token": "",
    },
    "setup_done": False,
}

_PARAM = re.compile(r"<(?:(path):)?([a-zA-Z_][a-zA-Z0-9_]*)>")


def compile_pattern(p):
    out, pos = [], 0
    for m in _PARAM.finditer(p):
        out.append(re.escape(p[pos:m.start()]))
        out.append("(?P<%s>%s)" % (m.group(2), ".+" if m.group(1) else "[^/]+"))
        pos = m.end()
    out.append(re.escape(p[pos:]))
    return re.compile("^" + "".join(out) + "$")


class Route:
    def __init__(self, method, pattern, handler, access, owner, prio, order):
        self.method = method.upper()
        self.pattern = pattern
        self.regex = compile_pattern(pattern)
        self.handler = handler
        self.access = access  # public | user | admin
        self.owner = owner
        self.prio = prio
        self.order = order


class App:
    def __init__(self, config_path=None, data_dir=None):
        self.root = ROOT
        self.version = __version__
        self.config_path = str(config_path or os.environ.get("SCC_CONFIG") or ROOT / "config.json")
        self.data_dir = str(data_dir or os.environ.get("SCC_DATA") or ROOT / "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.started = time.time()
        self.routes = []
        self._sorted = None
        self._order = 0
        self.hooks = {}
        self.nav = []
        self.heads = []
        self.assets = []   # (owner, kind, url)
        self.restorers = []  # (owner, fn)
        self.server = None
        self.exit_code = 0
        self.restart_requested = False
        self.config = deep_merge(DEFAULT_CONFIG, read_json(self.config_path, {}) or {})
        self.setup_token = None
        self.log = self._setup_logging()
        # подсистемы импортируются здесь, чтобы избежать циклов
        from .auth import Auth
        from .modules import ModuleManager
        from .updater import Updater
        self.auth = Auth(self)
        self.modules = ModuleManager(self)
        self.updater = Updater(self)

    # ---------------- logging ----------------
    def _setup_logging(self):
        log = logging.getLogger("scc")
        if log.handlers:
            return log
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
        try:
            ld = os.path.join(self.data_dir, "logs")
            os.makedirs(ld, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(ld, "scc.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
        return log

    # ---------------- config ----------------
    @property
    def type(self):
        return self.config.get("type")

    @property
    def configured(self):
        return bool(self.config.get("setup_done")) and self.config.get("type") in TYPES

    def save_config(self):
        with self.lock:
            write_json(self.config_path, self.config, private=True)

    def ensure_setup_token(self):
        if self.configured:
            return None
        if not self.setup_token:
            self.setup_token = secrets.token_urlsafe(9)
            try:
                p = os.path.join(self.data_dir, "setup_token.txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(self.setup_token + "\n")
            except OSError:
                pass
        return self.setup_token

    def clear_setup_token(self):
        self.setup_token = None
        try:
            os.unlink(os.path.join(self.data_dir, "setup_token.txt"))
        except OSError:
            pass

    # ---------------- routes ----------------
    def add_route(self, method, pattern, handler, access="admin", owner="core", prio=0):
        with self.lock:
            self._order += 1
            self.routes.append(Route(method, pattern, handler, access, owner, prio, self._order))
            self._sorted = None

    def match(self, method, path):
        with self.lock:
            if self._sorted is None:
                self._sorted = sorted(self.routes, key=lambda r: (-r.prio, r.order))
            routes = self._sorted
        other = False
        for r in routes:
            m = r.regex.match(path)
            if not m:
                continue
            if r.method in (method, "*") or (method == "HEAD" and r.method == "GET"):
                return r, m.groupdict(), False
            other = True
        return None, None, other

    # ---------------- hooks ----------------
    def hook(self, event, fn, owner="core", prio=0):
        with self.lock:
            self._order += 1
            self.hooks.setdefault(event, []).append((-prio, self._order, owner, fn))
            self.hooks[event].sort(key=lambda x: (x[0], x[1]))

    def emit(self, event, *a, **kw):
        res = []
        for _, _, owner, fn in list(self.hooks.get(event, [])):
            try:
                res.append(fn(*a, **kw))
            except Exception:
                self.log.exception("hook %s (%s) failed", event, owner)
        return res

    def first(self, event, *a, **kw):
        """Вызывать хуки по порядку, пока один не вернёт не-None."""
        for _, _, owner, fn in list(self.hooks.get(event, [])):
            try:
                r = fn(*a, **kw)
            except Exception:
                self.log.exception("hook %s (%s) failed", event, owner)
                continue
            if r is not None:
                return r
        return None

    def filter(self, event, value, *a, **kw):
        for _, _, owner, fn in list(self.hooks.get(event, [])):
            try:
                r = fn(value, *a, **kw)
                if r is not None:
                    value = r
            except Exception:
                self.log.exception("filter %s (%s) failed", event, owner)
        return value

    # ---------------- registrations ----------------
    def unregister_owner(self, owner):
        with self.lock:
            self.routes = [r for r in self.routes if r.owner != owner]
            self._sorted = None
            for ev in list(self.hooks):
                self.hooks[ev] = [h for h in self.hooks[ev] if h[2] != owner]
            self.nav = [n for n in self.nav if n.get("owner") != owner]
            self.heads = [h for h in self.heads if h[0] != owner]
            self.assets = [a for a in self.assets if a[0] != owner]
            rest = [(o, fn) for o, fn in self.restorers if o == owner]
            self.restorers = [(o, fn) for o, fn in self.restorers if o != owner]
        for _, fn in reversed(rest):
            try:
                fn()
            except Exception:
                self.log.exception("restorer of %s failed", owner)

    # ---------------- lifecycle ----------------
    def uptime(self):
        return time.time() - self.started

    def request_restart(self):
        self.restart_requested = True
        self.exit_code = 3
        self.request_stop()

    def request_stop(self):
        srv = self.server
        if srv:
            threading.Thread(target=lambda: (time.sleep(0.6), srv.shutdown()), daemon=True).start()
