"""Пользователи, сессии, токен ноды, защита от подбора пароля."""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from .util import read_json, write_json

USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ITERATIONS = 260_000
MIN_PASSWORD = 8
ROLES = ("admin", "viewer")


class AuthError(Exception):
    def __init__(self, message, status=401):
        Exception.__init__(self, message)
        self.status = status


def hash_password(password, salt=None, iters=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return {"salt": salt.hex(), "hash": dk.hex(), "iter": iters}


def check_password(password, rec):
    salt = bytes.fromhex(rec["salt"])
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rec["iter"]))
    return hmac.compare_digest(dk.hex(), rec["hash"])


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Auth:
    MAX_FAILS = 5
    LOCK_SECONDS = 300

    def __init__(self, app):
        self.app = app
        self.users_file = os.path.join(app.data_dir, "users.json")
        self.sessions_file = os.path.join(app.data_dir, "sessions.json")
        self.lock = threading.RLock()
        self.users = (read_json(self.users_file, {}) or {}).get("users", {})
        self.sessions = read_json(self.sessions_file, {}) or {}
        self.fails = {}
        self._dummy = hash_password("dummy-password")
        self._purge()

    # ---------------- users ----------------
    def _save_users(self):
        write_json(self.users_file, {"users": self.users}, private=True)

    def _save_sessions(self):
        write_json(self.sessions_file, self.sessions, private=True)

    def has_users(self):
        return bool(self.users)

    def list_users(self):
        with self.lock:
            return [{"name": n, "role": u.get("role", "admin"), "created": u.get("created")}
                    for n, u in sorted(self.users.items())]

    @staticmethod
    def check_new_password(pw):
        if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
            raise AuthError("Пароль должен содержать минимум %d символов" % MIN_PASSWORD, 400)
        if len(pw) > 256:
            raise AuthError("Пароль слишком длинный", 400)

    def create_user(self, name, password, role="admin"):
        if not isinstance(name, str) or not USER_RE.match(name):
            raise AuthError("Имя пользователя: 3-32 символа, латиница, цифры, . _ -", 400)
        if role not in ROLES:
            raise AuthError("Неизвестная роль", 400)
        self.check_new_password(password)
        with self.lock:
            if name in self.users:
                raise AuthError("Пользователь уже существует", 409)
            rec = hash_password(password)
            rec.update({"role": role, "created": int(time.time())})
            self.users[name] = rec
            self._save_users()

    def set_password(self, name, password):
        self.check_new_password(password)
        with self.lock:
            if name not in self.users:
                raise AuthError("Пользователь не найден", 404)
            role, created = self.users[name].get("role", "admin"), self.users[name].get("created")
            rec = hash_password(password)
            rec.update({"role": role, "created": created})
            self.users[name] = rec
            self._save_users()
        self.revoke_user(name)

    def set_role(self, name, role):
        if role not in ROLES:
            raise AuthError("Неизвестная роль", 400)
        with self.lock:
            if name not in self.users:
                raise AuthError("Пользователь не найден", 404)
            if self.users[name].get("role") == "admin" and role != "admin" and self._admins() <= 1:
                raise AuthError("Нельзя убрать права у последнего администратора", 400)
            self.users[name]["role"] = role
            self._save_users()

    def _admins(self):
        return sum(1 for u in self.users.values() if u.get("role", "admin") == "admin")

    def delete_user(self, name):
        with self.lock:
            if name not in self.users:
                raise AuthError("Пользователь не найден", 404)
            if self.users[name].get("role", "admin") == "admin" and self._admins() <= 1:
                raise AuthError("Нельзя удалить последнего администратора", 400)
            del self.users[name]
            self._save_users()
        self.revoke_user(name)

    def verify(self, name, password):
        rec = self.users.get(name) if isinstance(name, str) else None
        if rec is None:
            check_password(password or "", self._dummy)  # выравнивание времени
            return False
        return check_password(password or "", rec)

    # ---------------- throttling ----------------
    def _check_lock(self, ip):
        f = self.fails.get(ip)
        if f and f[1] > time.time():
            raise AuthError("Слишком много неудачных попыток. Повторите через %d с." % int(f[1] - time.time()), 429)

    def _fail(self, ip):
        f = self.fails.get(ip, [0, 0])
        if f[1] and f[1] <= time.time():
            f = [0, 0]
        f[0] += 1
        if f[0] >= self.MAX_FAILS:
            f[1] = time.time() + self.LOCK_SECONDS
            f[0] = 0
        self.fails[ip] = f
        if len(self.fails) > 5000:
            self.fails.clear()

    # ---------------- sessions ----------------
    def login(self, name, password, ip):
        with self.lock:
            self._check_lock(ip)
            if not self.verify(name, password):
                self._fail(ip)
                raise AuthError("Неверное имя пользователя или пароль", 401)
            self.fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            ttl = int(float(self.app.config.get("session_hours", 12)) * 3600)
            self.sessions[sha256(token)] = {
                "user": name,
                "csrf": secrets.token_urlsafe(24),
                "created": int(time.time()),
                "exp": int(time.time()) + ttl,
            }
            self._purge()
            self._save_sessions()
        return token

    def session_for(self, token):
        if not token:
            return None
        with self.lock:
            s = self.sessions.get(sha256(token))
            if not s:
                return None
            if s["exp"] < time.time():
                self.sessions.pop(sha256(token), None)
                return None
            u = self.users.get(s["user"])
            if not u:
                return None
            return {"name": s["user"], "role": u.get("role", "admin"), "csrf": s["csrf"], "via": "session"}

    def logout(self, token):
        with self.lock:
            if self.sessions.pop(sha256(token or ""), None) is not None:
                self._save_sessions()

    def revoke_user(self, name):
        with self.lock:
            for k in [k for k, v in self.sessions.items() if v["user"] == name]:
                del self.sessions[k]
            self._save_sessions()

    def _purge(self):
        now = time.time()
        for k in [k for k, v in self.sessions.items() if v.get("exp", 0) < now or v.get("user") not in self.users]:
            del self.sessions[k]

    # ---------------- node token ----------------
    def new_node_token(self):
        token = "scn_" + secrets.token_urlsafe(32)
        self.app.config.setdefault("node", {})["token_hash"] = sha256(token)
        self.app.save_config()
        return token

    def check_node_token(self, token, ip):
        h = self.app.config.get("node", {}).get("token_hash")
        if not h or not token:
            return None
        with self.lock:
            self._check_lock(ip)
            if hmac.compare_digest(sha256(token), h):
                return {"name": "@panel", "role": "admin", "via": "token"}
            self._fail(ip)
        return None
