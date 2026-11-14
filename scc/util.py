"""Мелкие общие помощники (stdlib only)."""
import json
import os
import re
import subprocess
import tempfile

IS_WIN = os.name == "nt"
PLATFORM = "windows" if IS_WIN else ("darwin" if os.uname().sysname == "Darwin" else "linux")


def read_json(path, default=None):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data, private=False):
    """Атомарная запись JSON (tmp + replace)."""
    path = str(path)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if not IS_WIN:
            os.chmod(tmp, 0o600 if private else 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def vkey(tag):
    """Ключ сравнения версий: v1.2.3 / 1.2.3-rc1. None, если тег не числовой."""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)(.*)$", str(tag).strip())
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split(".")]
    nums += [0] * (4 - len(nums))
    pre = 0 if m.group(2).startswith("-") else 1
    return (tuple(nums), pre)


def run_cmd(args, timeout=30, shell=False, input=None, cwd=None, env=None):
    """Запустить команду и вернуть {code, out, err}. Никогда не бросает исключений."""
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(
            args, shell=shell, input=input, cwd=cwd, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw
        )
        return {
            "code": p.returncode,
            "out": p.stdout.decode("utf-8", "replace"),
            "err": p.stderr.decode("utf-8", "replace"),
        }
    except FileNotFoundError as e:
        return {"code": 127, "out": "", "err": "Команда не найдена: %s" % e}
    except subprocess.TimeoutExpired:
        return {"code": -1, "out": "", "err": "Превышено время ожидания (%ss)" % timeout}
    except Exception as e:  # noqa
        return {"code": -2, "out": "", "err": str(e)}
