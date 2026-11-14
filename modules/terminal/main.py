import os
import shutil
import subprocess
import threading

IS_WIN = os.name == "nt"
MAX_OUT = 1024 * 1024
_cwd = {}
_lock = threading.Lock()


def _shell_cmd(cmd):
    if IS_WIN:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            return [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd]
        return ["cmd", "/c", cmd]
    return [shutil.which("bash") or "/bin/sh", "-c", cmd]


def setup(api):
    home = os.path.expanduser("~")

    @api.route("GET", "state")
    def state(req):
        with _lock:
            cwd = _cwd.get(req.user["name"], home)
        return {"cwd": cwd, "shell": "powershell" if IS_WIN else "bash", "user": req.user["name"]}

    @api.route("POST", "run")
    def run(req):
        d = req.json()
        cmd = str(d.get("cmd", ""))
        timeout = max(1, min(600, int(d.get("timeout") or 60)))
        user = req.user["name"]
        with _lock:
            cwd = _cwd.get(user, home)
        stripped = cmd.strip()
        if stripped == "cd" or stripped.startswith("cd ") or stripped.lower().startswith("set-location "):
            target = stripped.split(None, 1)[1].strip().strip('"\'') if " " in stripped else home
            target = os.path.expanduser(target)
            new = os.path.realpath(os.path.join(cwd, target))
            if os.path.isdir(new):
                with _lock:
                    _cwd[user] = new
                return {"out": "", "err": "", "code": 0, "cwd": new}
            return {"out": "", "err": "cd: нет такого каталога: %s\n" % target, "code": 1, "cwd": cwd}
        kw = {"creationflags": 0x08000000} if IS_WIN else {}
        try:
            p = subprocess.run(_shell_cmd(cmd), cwd=cwd, timeout=timeout, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            out, err, code = e.stdout or b"", (e.stderr or b"") + ("\n[превышено время %d с, процесс остановлен]\n" % timeout).encode(), -1
        except Exception as e:
            out, err, code = b"", str(e).encode(), -2
        dec = lambda b: b[:MAX_OUT].decode("utf-8", "replace") + ("\n[вывод обрезан]" if len(b) > MAX_OUT else "")
        api.log.info("terminal[%s]: %s", user, cmd[:200])
        return {"out": dec(out), "err": dec(err), "code": code, "cwd": cwd}
