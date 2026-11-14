#!/usr/bin/env python3
"""Собирает архив релиза для GitHub Releases.

    python tools/build_release.py                 # версия из файла VERSION
    python tools/build_release.py --version 1.2.3 # записать версию в архив (CI подставляет её из тега)

Результат: dist/server-control-web.zip и dist/server-control-web.zip.sha256
Именно этот файл скачивает автообновление (маска asset по умолчанию: server-control-web*.zip).
"""
import argparse
import hashlib
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["VERSION", "run.py", "run.sh", "run.bat", "README.md", "LICENSE", "config.example.json",
           "scc", "modules", "deploy", "docs", "tests", "tools"]
SKIP_DIRS = {"__pycache__", ".git", "data", "dist", ".github", "node_modules"}
SKIP_FILES = {"config.json"}
EXEC = {"run.sh", "deploy/install-linux.sh"}


def collect():
    for item in INCLUDE:
        p = os.path.join(ROOT, item)
        if os.path.isfile(p):
            yield item
        elif os.path.isdir(p):
            for dp, dns, fns in os.walk(p):
                dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
                for fn in sorted(fns):
                    if fn.endswith((".pyc", ".pyo")) or fn in SKIP_FILES:
                        continue
                    yield os.path.relpath(os.path.join(dp, fn), ROOT).replace(os.sep, "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="версия для записи в VERSION (например 1.2.3 или v1.2.3)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--name", default="server-control-web.zip")
    a = ap.parse_args()

    version = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    if a.version:
        version = a.version.lstrip("vV")
        if not re.match(r"^\d+(\.\d+)*([-+].+)?$", version):
            sys.exit("Некорректная версия: %s" % a.version)
    os.makedirs(a.out, exist_ok=True)
    dest = os.path.join(a.out, a.name)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in collect():
            info = zipfile.ZipInfo(rel, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel in EXEC else 0o644) << 16
            if rel == "VERSION":
                z.writestr(info, version + "\n")
            else:
                with open(os.path.join(ROOT, rel), "rb") as f:
                    z.writestr(info, f.read())
    digest = hashlib.sha256(open(dest, "rb").read()).hexdigest()
    with open(dest + ".sha256", "w") as f:
        f.write("%s  %s\n" % (digest, os.path.basename(dest)))
    print("Версия %s -> %s (%d КБ)\nSHA-256: %s" % (version, dest, os.path.getsize(dest) // 1024, digest))


if __name__ == "__main__":
    main()
