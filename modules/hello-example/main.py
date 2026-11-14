"""Пример модуля. Скопируйте папку, поменяйте id в module.json - и пишите своё.

Модуль работает внутри процесса панели и может ВСЁ: менять сайт, ядро и систему.
Всё, что регистрируется через api.*, автоматически снимается при выключении модуля.
"""
import time

from scc.server import Response


def setup(api):
    app = api.app          # всё приложение: app.config, app.auth, app.modules, app.updater, app.routes ...
    api.log.info("Привет из модуля %s! Платформа: %s", api.id, api.platform)

    # 1. Свои настройки (data/modules/hello-example.json)
    api.config.setdefault("greeting", "Привет из модуля!")
    api.config.setdefault("accent", "")          # например "#8250df" - перекрасит сайт

    # 2. Маршруты API: /api/m/hello-example/...   access: "public" | "user" (любой вошедший) | "admin"
    @api.route("GET", "hello", access="user")
    def hello(req):
        return {"message": api.config["greeting"], "user": req.user["name"], "time": time.time(),
                "accent": api.config["accent"]}

    @api.route("POST", "greeting")
    def set_greeting(req):
        d = req.json()
        api.config["greeting"] = str(d.get("greeting", ""))[:200]
        api.config["accent"] = str(d.get("accent", ""))[:20]
        api.save_config()
        return {"ok": True}

    # 3. Системная команда (на Windows и Linux)
    @api.route("GET", "uptime")
    def uptime(req):
        r = api.run("net statistics workstation" if api.is_windows else "uptime -p", shell=True, timeout=10)
        return {"out": (r["out"] or r["err"]).strip()}

    # 4. Хук на каждый ответ сервера: можно менять заголовки и тело
    @api.hook("response")
    def add_header(resp, req):
        resp.headers["X-Hello-Module"] = "on"
        return resp

    # 5. Перехват любого запроса ДО маршрутизации (вернули Response - запрос обработан вами)
    @api.hook("request")
    def intercept(req):
        if req.path == "/hello-module.txt":
            return Response.text("Этот ответ создан модулем hello-example\n")

    # 6. Свой маршрут по любому пути; prio > 0 перекрывает системные маршруты
    # api.raw_route("GET", "/api/boot", my_boot, access="public", prio=10)

    # 7. Правка главной страницы (HTML целиком) и <head>
    api.inject_head('<meta name="generator" content="Server Control Web + hello-example">')

    @api.hook("page_html")
    def tweak_html(html):
        return html.replace("<title>Server Control</title>", "<title>Server Control (с модулем)</title>")

    # 8. Подмена любой функции ядра (откатится при выключении модуля)
    #    Пример: писать в журнал каждый успешный вход.
    original_login = app.auth.login

    def login_with_log(name, password, ip):
        token = original_login(name, password, ip)
        api.log.info("Вход пользователя %s с адреса %s", name, ip)
        return token
    api.patch(app.auth, "login", login_with_log)

    # 9. Фоновый поток и очистка
    # api.on_unload(lambda: ...)   - выполнить при выключении модуля


def teardown(api):
    api.log.info("Модуль выключен")
