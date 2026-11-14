import os
import platform
import shutil
import socket
import threading
import time

IS_WIN = os.name == "nt"
_stop = threading.Event()
_cpu = {"pct": None, "per": []}


def _read_cpu_linux():
    with open("/proc/stat") as f:
        out = []
        for line in f:
            if line.startswith("cpu"):
                p = line.split()
                v = [int(x) for x in p[1:9]]
                idle = v[3] + v[4]
                out.append((sum(v), idle))
        return out


def _read_cpu_win():
    import ctypes
    from ctypes import wintypes

    class FT(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]
    i, k, u = FT(), FT(), FT()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    val = lambda f: (f.hi << 32) | f.lo
    idle, kern, user = val(i), val(k), val(u)
    return [(kern + user, idle)]  # kernel включает idle


def _sampler():
    read = _read_cpu_win if IS_WIN else _read_cpu_linux
    try:
        prev = read()
    except Exception:
        return
    while not _stop.wait(2.0):
        try:
            cur = read()
        except Exception:
            continue
        pct = []
        for (t1, i1), (t2, i2) in zip(prev, cur):
            dt = t2 - t1
            pct.append(round(100.0 * (1 - (i2 - i1) / dt), 1) if dt > 0 else 0.0)
        if pct:
            _cpu["pct"], _cpu["per"] = pct[0], pct[1:]
        prev = cur


def memory():
    if IS_WIN:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                        ("ae", ctypes.c_ulonglong)]
        m = MS(); m.l = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {"total": m.tp, "used": m.tp - m.ap, "swap_total": max(0, m.tpf - m.tp), "swap_used": max(0, (m.tpf - m.apf) - (m.tp - m.ap))}
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    st, sf = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    return {"total": total, "used": total - avail, "swap_total": st, "swap_used": st - sf}


def disks():
    out = []
    if IS_WIN:
        import string
        for l in string.ascii_uppercase:
            p = l + ":\\"
            if os.path.exists(p):
                try:
                    u = shutil.disk_usage(p)
                    out.append({"mount": p, "total": u.total, "used": u.used})
                except OSError:
                    pass
        return out
    seen = set()
    skip = ("tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay", "squashfs", "devpts", "mqueue", "debugfs",
            "tracefs", "securityfs", "pstore", "bpf", "autofs", "configfs", "fusectl", "hugetlbfs", "binfmt_misc", "nsfs", "ramfs", "efivarfs")
    try:
        with open("/proc/mounts") as f:
            for line in f:
                dev, mnt, fs = line.split()[:3]
                if fs in skip or dev in seen or mnt.startswith(("/snap", "/sys", "/proc", "/dev", "/run")):
                    continue
                seen.add(dev)
                try:
                    u = shutil.disk_usage(mnt)
                    if u.total:
                        out.append({"mount": mnt, "total": u.total, "used": u.used, "fs": fs})
                except OSError:
                    pass
    except OSError:
        pass
    return out


def uptime():
    if IS_WIN:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetTickCount64.restype = ctypes.c_ulonglong
        return k.GetTickCount64() / 1000.0
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def os_name():
    if not IS_WIN:
        try:
            with open("/etc/os-release") as f:
                d = dict(l.strip().split("=", 1) for l in f if "=" in l)
            return d.get("PRETTY_NAME", "").strip('"') or platform.platform()
        except OSError:
            pass
    return platform.platform()


def setup(api):
    _stop.clear()
    threading.Thread(target=_sampler, daemon=True, name="dashboard-sampler").start()

    @api.route("GET", "stats", access="user")
    def stats(req):
        try:
            load = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        except OSError:
            load = None
        return {
            "hostname": socket.gethostname(), "os": os_name(), "kernel": platform.release(), "arch": platform.machine(),
            "python": platform.python_version(), "cpu_count": os.cpu_count(), "cpu": _cpu["pct"], "cpu_per": _cpu["per"],
            "load": load, "memory": memory(), "disks": disks(), "uptime": uptime(), "app_uptime": api.app.uptime(),
            "time": time.time(), "version": api.app.version, "type": api.app.type,
        }


def teardown(api):
    _stop.set()
