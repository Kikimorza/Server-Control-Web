"""Службы: systemd на Linux, Service Control Manager (PowerShell) на Windows.

Имена служб никогда не попадают в командную строку через shell: на Linux они передаются
отдельными аргументами, на Windows - через переменную окружения SCC_SVC.
"""
import json
import os
import re
import shutil

from scc.server import HTTPError

UNIT_RE = re.compile(r"^[A-Za-z0-9@._:\\-]{1,128}$")
WIN_RE = re.compile(r"^[A-Za-z0-9@._:$# \-]{1,128}$")
ACTIONS = ("start", "stop", "restart", "enable", "disable")


def setup(api):
    win = api.is_windows

    # ------------------------------------------------------------ Windows
    def ps(script, svc=None, timeout=40):
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise HTTPError(501, "PowerShell не найден")
        env = dict(os.environ)
        if svc is not None:
            env["SCC_SVC"] = svc
        return api.run([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script], timeout=timeout, env=env)

    def list_windows():
        r = ps("Get-Service | Select-Object Name,DisplayName,"
               "@{n='Status';e={$_.Status.ToString()}},@{n='StartType';e={$_.StartType.ToString()}} | ConvertTo-Json -Compress")
        if r["code"] != 0:
            raise HTTPError(500, (r["err"] or "Не удалось получить список служб").strip()[:300])
        data = json.loads(r["out"] or "[]")
        if isinstance(data, dict):
            data = [data]
        out = []
        for s in data:
            st = (s.get("Status") or "").lower()
            out.append({"name": s["Name"], "display": s.get("DisplayName") or "",
                        "active": "active" if st == "running" else "inactive", "sub": st,
                        "enabled": (s.get("StartType") or "").lower()})
        return out

    def act_windows(name, action):
        script = {
            "start": "Start-Service -Name $env:SCC_SVC -ErrorAction Stop",
            "stop": "Stop-Service -Name $env:SCC_SVC -Force -ErrorAction Stop",
            "restart": "Restart-Service -Name $env:SCC_SVC -Force -ErrorAction Stop",
            "enable": "Set-Service -Name $env:SCC_SVC -StartupType Automatic -ErrorAction Stop",
            "disable": "Set-Service -Name $env:SCC_SVC -StartupType Disabled -ErrorAction Stop",
        }[action]
        return ps(script, name, timeout=90)

    def logs_windows(name):
        r = ps("try { Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName=$env:SCC_SVC} -MaxEvents 100 -ErrorAction Stop | "
               "ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}' -f $_.TimeCreated,$_.LevelDisplayName,$_.Message } } "
               "catch { 'Нет событий журнала System с источником ' + $env:SCC_SVC }", name)
        return (r["out"] or r["err"]).strip()

    # ------------------------------------------------------------ Linux
    def need_systemd():
        if not shutil.which("systemctl"):
            raise HTTPError(501, "systemd не найден на этом сервере")

    def systemctl(args, write=False, timeout=60):
        cmd = ["systemctl"] + args
        if write and hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
            cmd = ["sudo", "-n"] + cmd
        return api.run(cmd, timeout=timeout)

    def list_linux():
        need_systemd()
        r = systemctl(["list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"])
        if r["code"] != 0 and not r["out"]:
            if "not been booted" in r["err"] or "Failed to connect to bus" in r["err"]:
                raise HTTPError(501, "systemd не запущен в этой среде (контейнер без init?)")
            raise HTTPError(500, (r["err"] or "systemctl вернул ошибку").strip()[:300])
        en = {}
        f = systemctl(["list-unit-files", "--type=service", "--no-legend", "--no-pager", "--plain"])
        for line in f["out"].splitlines():
            p = line.split()
            if len(p) >= 2:
                en[p[0]] = p[1]
        out = []
        for line in r["out"].splitlines():
            line = line.strip().lstrip("●*").strip()
            p = line.split(None, 4)
            if len(p) < 4 or not p[0].endswith(".service"):
                continue
            out.append({"name": p[0], "display": p[4] if len(p) > 4 else "", "active": p[2], "sub": p[3],
                        "enabled": en.get(p[0], "")})
        return out

    # ------------------------------------------------------------ API
    def check_name(name):
        if not (WIN_RE if win else UNIT_RE).match(name or ""):
            raise HTTPError(400, "Недопустимое имя службы")

    @api.route("GET", "list", access="user")
    def list_services(req):
        svcs = list_windows() if win else list_linux()
        svcs.sort(key=lambda s: s["name"].lower())
        return {"services": svcs, "platform": "windows" if win else "linux"}

    @api.route("POST", "<name>/<action>")
    def action(req):
        name, act = req.params["name"], req.params["action"]
        check_name(name)
        if act not in ACTIONS:
            raise HTTPError(400, "Неизвестное действие")
        if win:
            r = act_windows(name, act)
        else:
            need_systemd()
            r = systemctl([act, "--", name], write=True, timeout=90)
        api.log.info("services[%s]: %s %s -> %s", req.user["name"], act, name, r["code"])
        if r["code"] != 0:
            raise HTTPError(500, (r["err"] or r["out"] or "Команда завершилась с ошибкой").strip()[:500])
        return {"ok": True}

    @api.route("GET", "<name>/logs")
    def logs(req):
        name = req.params["name"]
        check_name(name)
        if win:
            return {"log": logs_windows(name)}
        need_systemd()
        r = api.run(["journalctl", "-u", name, "-n", "300", "--no-pager"], timeout=30)
        return {"log": (r["out"] or r["err"]).strip()}
