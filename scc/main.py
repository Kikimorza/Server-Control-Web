"""Точка входа: аргументы, запуск сервера, перезапуск."""
import argparse
import os
import signal
import socket
import subprocess
import sys

from . import __version__


def _ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Server Control Web %s" % __version__)
    ap.add_argument("--config", help="путь к config.json")
    ap.add_argument("--data", help="каталог данных")
    ap.add_argument("--type", choices=["panel", "node"], help="тип установки (до первичной настройки)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--rollback", action="store_true", help="откатить последнее обновление и выйти")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    from .core import App
    app = App(args.config, args.data)

    if args.rollback:
        print("Восстановлено из резервной копии:", app.updater.rollback())
        return 0

    if args.type and not app.configured:
        app.config["type"] = args.type
    if args.host:
        app.config["host"] = args.host
    if args.port:
        app.config["port"] = args.port

    from . import api_core
    from .server import WebServer

    api_core.register(app)
    if app.configured:
        app.modules.load_all()
    else:
        app.ensure_setup_token()

    try:
        server = WebServer(app)
    except OSError as e:
        print("Не удалось запустить сервер на %s:%s - %s" % (app.config["host"], app.config["port"], e))
        return 2
    app.server = server

    scheme = "https" if server.https else "http"
    port = app.config["port"]
    print("=" * 64)
    print(" Server Control Web %s" % __version__)
    print(" Тип: %s" % (app.type or "не выбран (будет выбран в мастере)"))
    print(" Адреса: %s" % ", ".join("%s://%s:%s" % (scheme, h, port) for h in ["localhost"] + _ips()))
    if not app.configured:
        print(" ПЕРВЫЙ ЗАПУСК. Откройте адрес в браузере и пройдите настройку.")
        print(" Код установки: %s" % app.setup_token)
    print("=" * 64, flush=True)

    def stop(*_):
        app.exit_code = 0
        app.request_stop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):
            pass

    app.updater.start_background()
    app.emit("startup")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        app.emit("shutdown")
        server.server_close()

    if app.restart_requested:
        if os.environ.get("SCC_SUPERVISED"):
            return 3  # run.sh / run.bat перезапустят процесс
        print("Перезапуск...", flush=True)
        exe, argv2 = sys.executable, [sys.executable] + sys.argv
        if os.name == "nt":
            subprocess.Popen(argv2)
            return 0
        os.execv(exe, argv2)
    return app.exit_code
