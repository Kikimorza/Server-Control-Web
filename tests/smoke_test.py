#!/usr/bin/env python3
"""Сквозная проверка: поднимает панель и ноду во временных каталогах и проходит основные сценарии.

    python tests/smoke_test.py

Работает на Linux и Windows, ничего не устанавливает и не трогает ваш config.json / data.
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  -> " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def call(self, method, path, data=None, raw=None, headers=None, csrf=True):
        h = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        elif raw is not None:
            body = raw
        if self.csrf and csrf:
            h["X-SCC-CSRF"] = self.csrf
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with self.op.open(req, timeout=30) as r:
                b = r.read()
                return r.status, self._parse(r.headers, b), r.headers
        except urllib.error.HTTPError as e:
            b = e.read()
            return e.code, self._parse(e.headers, b), e.headers

    @staticmethod
    def _parse(headers, b):
        if "json" in (headers.get("Content-Type") or ""):
            try:
                return json.loads(b.decode())
            except ValueError:
                pass
        return b

    def refresh_csrf(self):
        st, boot, _ = self.call("GET", "/api/boot")
        self.csrf = boot.get("csrf")
        return boot


class Instance:
    def __init__(self, tmp, name, port, typ=None):
        self.dir = os.path.join(tmp, name)
        os.makedirs(self.dir)
        self.port = port
        self.cfg = os.path.join(self.dir, "config.json")
        if typ:
            with open(self.cfg, "w") as f:
                json.dump({"type": typ}, f)
        self.log = open(os.path.join(self.dir, "out.log"), "w")
        env = dict(os.environ, SCC_SUPERVISED="1", PYTHONUTF8="1")
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "run.py"), "--config", self.cfg, "--data", os.path.join(self.dir, "data"),
                                   "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.url = "http://127.0.0.1:%d" % port
        for _ in range(60):
            try:
                urllib.request.urlopen(self.url + "/api/boot", timeout=1).read()
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("сервер %s не запустился" % name)

    def token(self):
        with open(os.path.join(self.dir, "data", "setup_token.txt")) as f:
            return f.read().strip()

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(8)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.log.close()


def main():
    tmp = tempfile.mkdtemp(prefix="scc-smoke-")
    panel = node = None
    try:
        print("== запуск")
        panel = Instance(tmp, "panel", 18181, typ="panel")
        node = Instance(tmp, "node", 18182)
        P, N = Client(panel.url), Client(node.url)

        print("== мастер первой настройки")
        b = P.call("GET", "/api/boot")[1]
        check("до настройки configured=false, тип задан в конфиге", b["configured"] is False and b["setup"]["type_preset"] is True)
        check("страница входа недоступна до настройки", P.call("POST", "/api/login", {"username": "a", "password": "b"})[0] == 409)
        st, r, _ = P.call("POST", "/api/setup", {"setup_token": "wrong", "username": "admin", "password": "Passw0rd!x"})
        check("неверный код установки -> 403", st == 403, r)
        st, r, _ = P.call("POST", "/api/setup", {"setup_token": panel.token(), "username": "admin", "password": "short"})
        check("короткий пароль отклонён", st == 400, r)
        st, r, _ = P.call("POST", "/api/setup", {"setup_token": panel.token(), "username": "admin", "password": "Passw0rd!x", "name": "Тест-панель"})
        check("настройка панели", st == 200 and r.get("ok"), r)
        check("повторная настройка запрещена", P.call("POST", "/api/setup", {"setup_token": "x", "username": "u2u", "password": "Passw0rd!x"})[0] == 409)
        boot = P.refresh_csrf()
        check("после настройки вход выполнен", boot["authenticated"] and boot["user"]["role"] == "admin")
        check("тип = panel, имя сохранено", boot["type"] == "panel" and boot["name"] == "Тест-панель")
        navs = [n["id"] for n in boot["nav"]]
        check("в меню все встроенные модули", all(x in navs for x in ("dashboard", "terminal", "files", "services", "nodes")), navs)

        st, r, _ = N.call("POST", "/api/setup", {"setup_token": node.token(), "type": "node", "username": "admin", "password": "Passw0rd!x"})
        check("настройка ноды, выдан токен", st == 200 and str(r.get("node_token", "")).startswith("scn_"), r)
        node_token = r["node_token"]
        nb = N.refresh_csrf()
        check("на ноде модуль nodes не загружен", "nodes" not in [n["id"] for n in nb["nav"]])
        with open(node.cfg) as f:
            check("в config.json у ноды сохранён только хэш токена", node_token not in f.read())

        print("== безопасность")
        st, _, _ = P.call("POST", "/api/system/restart", {}, csrf=False)
        check("запись без CSRF-токена -> 403", st == 403)
        anon = Client(panel.url)
        check("API без входа -> 401", anon.call("GET", "/api/users")[0] == 401)
        check("без входа нельзя вызвать модуль", anon.call("GET", "/api/m/dashboard/stats")[0] == 401)
        st, _, hd = anon.call("GET", "/api/boot")
        check("заголовки безопасности", hd.get("X-Frame-Options") == "DENY" and "default-src 'self'" in (hd.get("Content-Security-Policy") or ""))
        check("traversal в статике не работает", anon.call("GET", "/static/..%2f..%2fconfig.json")[0] in (404,))
        check("токен ноды с неверным значением -> 401", Client(node.url).call("GET", "/api/agent/info", headers={"Authorization": "Bearer scn_bad"})[0] == 401)

        print("== встроенные модули (панель)")
        st, r, _ = P.call("GET", "/api/m/dashboard/stats")
        check("dashboard: статистика", st == 200 and r["memory"]["total"] > 0 and r["disks"], r if st != 200 else "")
        d = os.path.join(tmp, "fs")
        os.makedirs(d)
        st, r, _ = P.call("POST", "/api/m/files/create", {"path": d, "name": "hello.txt", "type": "file"})
        check("files: создать файл", st == 200, r)
        st, r, _ = P.call("PUT", "/api/m/files/write?path=" + urllib.request.quote(os.path.join(d, "hello.txt")), raw="Привет, мир".encode(), headers={"Content-Type": "text/plain"})
        check("files: записать", st == 200, r)
        st, r, _ = P.call("GET", "/api/m/files/read?path=" + urllib.request.quote(os.path.join(d, "hello.txt")))
        check("files: прочитать (UTF-8)", st == 200 and r["content"] == "Привет, мир", r)
        st, r, _ = P.call("GET", "/api/m/files/list?path=" + urllib.request.quote(d))
        check("files: список", st == 200 and [e["name"] for e in r["entries"]] == ["hello.txt"], r)
        check("files: переименование с '/' запрещено", P.call("POST", "/api/m/files/rename", {"path": os.path.join(d, "hello.txt"), "name": "../x"})[0] == 400)
        check("files: переименование", P.call("POST", "/api/m/files/rename", {"path": os.path.join(d, "hello.txt"), "name": "b.txt"})[0] == 200)
        st, r, _ = P.call("PUT", "/api/m/files/upload?path=%s&name=up.bin" % urllib.request.quote(d), raw=os.urandom(300000))
        check("files: загрузка", st == 200, r)
        check("files: корень удалять нельзя", P.call("POST", "/api/m/files/delete", {"path": os.sep})[0] == 400)
        check("files: удаление", P.call("POST", "/api/m/files/delete", {"path": os.path.join(d, "b.txt")})[0] == 200)
        st, r, _ = P.call("POST", "/api/m/terminal/run", {"cmd": "echo scc-ok"})
        check("terminal: команда", st == 200 and "scc-ok" in r["out"] and r["code"] == 0, r)
        st, r, _ = P.call("GET", "/api/m/services/list")
        check("services: ответ (список или 501 без systemd)", st in (200, 501), r)

        print("== ноды и прокси")
        st, r, _ = P.call("POST", "/api/m/nodes/add", {"name": "Нода 1", "url": node.url, "token": "scn_wrong"})
        check("nodes: неверный токен не принимается", st == 400, r)
        st, r, _ = P.call("POST", "/api/m/nodes/add", {"name": "Нода 1", "url": node.url, "token": node_token})
        check("nodes: добавление", st == 200 and r["info"]["type"] == "node", r)
        nid = r["node"]["id"]
        boot = P.refresh_csrf()
        check("nodes: нода в boot для шапки", [n["id"] for n in boot.get("nodes", [])] == [nid])
        st, r, _ = P.call("GET", "/api/m/nodes/status")
        check("nodes: статус online", st == 200 and r["nodes"][0]["online"] is True and "token" not in r["nodes"][0], r)
        st, r, _ = P.call("GET", "/node/%s/api/agent/info" % nid)
        check("прокси: agent/info ноды", st == 200 and r["type"] == "node", r)
        st, r, _ = P.call("GET", "/node/%s/api/m/dashboard/stats" % nid)
        check("прокси: модуль dashboard на ноде", st == 200 and r["type"] == "node", r)
        st, r, _ = P.call("POST", "/node/%s/api/m/terminal/run" % nid, {"cmd": "echo remote-ok"})
        check("прокси: terminal на ноде", st == 200 and "remote-ok" in r["out"], r)
        payload = os.urandom(2_500_000)
        st, r, _ = P.call("PUT", "/node/%s/api/m/files/upload?path=%s&name=big.bin" % (nid, urllib.request.quote(d)), raw=payload)
        check("прокси: загрузка 2.5 МБ на ноду", st == 200, r)
        st, r, _ = P.call("GET", "/node/%s/api/m/files/download?path=%s" % (nid, urllib.request.quote(os.path.join(d, "big.bin"))))
        check("прокси: скачивание совпадает", st == 200 and r == payload)
        check("прокси: неизвестная нода -> 404", P.call("GET", "/node/nope/api/agent/info")[0] == 404)
        check("прокси без входа -> 401", anon.call("GET", "/node/%s/api/agent/info" % nid)[0] == 401)
        st, r, _ = P.call("POST", "/api/m/nodes/%s/update" % nid, {"token": "scn_wrong"})
        check("nodes: смена токена на неверный отклонена", st == 400)
        # токен на ноде пересоздан -> панель получает 502, а не 401 (иначе UI разлогинил бы пользователя)
        st, r, _ = N.call("POST", "/api/settings/node-token", {})
        st, r2, _ = P.call("GET", "/node/%s/api/agent/info" % nid)
        check("устаревший токен ноды даёт 502 (а не 401)", st == 502, (st, r2))
        check("nodes: удаление", P.call("DELETE", "/api/m/nodes/%s" % nid)[0] == 200)

        print("== пользователи и права")
        check("создание наблюдателя", P.call("POST", "/api/users", {"username": "viewer1", "password": "Viewer-pass1", "role": "viewer"})[0] == 200)
        V = Client(panel.url)
        check("вход наблюдателя", V.call("POST", "/api/login", {"username": "viewer1", "password": "Viewer-pass1"})[0] == 200)
        vb = V.refresh_csrf()
        check("наблюдатель не видит список нод", "nodes" not in vb)
        check("наблюдатель: чтение статистики разрешено", V.call("GET", "/api/m/dashboard/stats")[0] == 200)
        check("наблюдатель: терминал запрещён", V.call("POST", "/api/m/terminal/run", {"cmd": "id"})[0] == 403)
        check("наблюдатель: файлы запрещены", V.call("GET", "/api/m/files/list?path=/")[0] == 403)
        check("наблюдатель: настройки запрещены", V.call("GET", "/api/settings")[0] == 403)
        check("наблюдатель: прокси на ноды запрещён", V.call("GET", "/node/x/api/agent/info")[0] == 403)
        check("нельзя удалить последнего админа", P.call("DELETE", "/api/users/admin")[0] == 400)
        check("смена пароля", P.call("POST", "/api/users/viewer1/password", {"password": "Another-pass2"})[0] == 200)
        check("старая сессия наблюдателя завершена", V.call("GET", "/api/users")[0] in (401, 403))

        print("== система модулей")
        st, r, _ = P.call("GET", "/api/modules")
        ids = {m["id"]: m for m in r["modules"]}
        check("список модулей, пример выключен", st == 200 and ids["hello-example"]["enabled"] is False and ids["files"]["loaded"] is True)
        # Интерфейс модулей: каждый подключаемый ui/css должен отдаваться как скрипт/стиль, а main.py - нет.
        st, ui, _ = P.call("GET", "/api/boot")
        bad = []
        for m in ui["modules"]:
            for url in ([m["ui"]] if m.get("ui") else []) + list(m.get("css") or []):
                s, body, hd = P.call("GET", url)
                ctype = hd.get("Content-Type") or ""
                if s != 200 or "json" in ctype:
                    bad.append((url, s, ctype))
        check("ui/css всех модулей отдаются (200, не JSON)", st == 200 and ui["modules"] and not bad, bad)
        check("серверный код модуля по HTTP недоступен", P.call("GET", "/m/dashboard/main.py")[0] == 404
              and P.call("GET", "/m/dashboard/web/../main.py")[0] in (400, 404))
        check("маршрут выключенного модуля недоступен", P.call("GET", "/api/m/hello-example/hello")[0] == 404)
        st, r, _ = P.call("POST", "/api/modules/hello-example/enable", {})
        check("включение модуля на лету", st == 200 and r["ok"], r)
        st, r, hd = P.call("GET", "/api/m/hello-example/hello")
        check("маршрут модуля работает", st == 200 and r["user"] == "admin", r)
        check("хук response добавил заголовок", hd.get("X-Hello-Module") == "on")
        st, r, _ = P.call("GET", "/hello-module.txt")
        check("хук request перехватывает запрос", st == 200 and b"hello-example" in r)
        check("page_html/inject_head меняют главную страницу", b"generator" in P.call("GET", "/")[1] and "с модулем".encode() in P.call("GET", "/")[1])
        P.call("POST", "/api/modules/hello-example/disable", {})
        check("выключение снимает маршруты и хуки", P.call("GET", "/api/m/hello-example/hello")[0] == 404 and "X-Hello-Module" not in P.call("GET", "/api/boot")[2])
        check("встроенный модуль нельзя удалить", P.call("DELETE", "/api/modules/files")[0] == 400)

        print("== настройки")
        st, r, _ = P.call("POST", "/api/settings", {"name": "Новое имя", "update": {"tag": "stable"}})
        check("сохранение настроек", st == 200, r)
        st, r, _ = P.call("GET", "/api/settings")
        check("настройки обновления применены", r["name"] == "Новое имя" and r["update"]["tag"] == "stable" and "token" not in r["update"])
        check("неверный порт отклонён", P.call("POST", "/api/settings", {"port": 70000})[0] == 400)

        print("== защита от подбора пароля (в конце: блокирует IP)")
        for i in range(5):
            anon.call("POST", "/api/login", {"username": "admin", "password": "bad%d" % i})
        check("блокировка после 5 неудачных входов", anon.call("POST", "/api/login", {"username": "admin", "password": "Passw0rd!x"})[0] == 429)

    finally:
        for i in (panel, node):
            if i:
                i.stop()
        if FAILS:
            for i in ("panel", "node"):
                try:
                    print("---- лог %s ----" % i)
                    print(open(os.path.join(tmp, i, "out.log")).read()[-3000:])
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%s" % ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else "ПРОВАЛЕНО: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
