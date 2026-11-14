#!/usr/bin/env python3
"""Генерирует scc/web/icons.js из настоящих иконок Octicons (MIT, © GitHub Inc.).

    python tools/gen_icons.py <каталог с *-16.svg>

Каталог с иконками есть, например, в пакете mkdocs-material:
    .../material/templates/.icons/octicons
или в npm-пакете @primer/octicons (папка icons/).
Имена слева - те, что используются в коде панели; справа - имена файлов Octicons.
"""
import json
import pathlib
import re
import sys

ALIASES = {
    "code": "code", "issue": "issue-opened", "pr": "git-pull-request", "server": "server",
    "terminal": "terminal", "file": "file", "folder": "file-directory", "folder-fill": "file-directory-fill",
    "folder-plus": "file-directory", "file-plus": "file-added", "file-code": "file-code",
    "file-zip": "file-zip", "file-binary": "file-binary", "file-media": "file-media",
    "gear": "gear", "plus": "plus", "search": "search", "bell": "bell", "inbox": "inbox",
    "check": "check", "x": "x", "chevron-down": "chevron-down", "chevron-right": "chevron-right",
    "chevron-up": "chevron-up", "chevron-left": "chevron-left", "sync": "sync",
    "download": "download", "upload": "upload", "trash": "trash", "pencil": "pencil",
    "play": "play", "stop": "stop", "alert": "alert", "alert-fill": "alert-fill", "info": "info",
    "shield": "shield", "shield-lock": "shield-lock", "shield-check": "shield-check",
    "pulse": "pulse", "cpu": "cpu", "database": "database", "package": "package", "plug": "plug",
    "book": "book", "eye": "eye", "eye-closed": "eye-closed", "star": "star", "lock": "lock",
    "unlock": "unlock", "key": "key", "person": "person", "people": "people",
    "sign-out": "sign-out", "sign-in": "sign-in", "sun": "sun", "moon": "moon",
    "menu": "three-bars", "kebab": "kebab-horizontal", "copy": "copy", "link": "link",
    "link-external": "link-external", "globe": "globe", "history": "history", "tag": "tag",
    "fork": "repo-forked", "pin": "pin", "power": "zap", "clock": "clock",
    "check-circle": "check-circle", "x-circle": "x-circle", "check-circle-fill": "check-circle-fill",
    "x-circle-fill": "x-circle-fill", "arrow-up": "arrow-up", "arrow-down": "arrow-down",
    "arrow-left": "arrow-left", "arrow-right": "arrow-right", "list": "list-unordered",
    "save": "check", "mod": "apps", "rocket": "rocket", "repo": "repo", "workflow": "workflow",
    "container": "container", "log": "log", "tools": "tools", "stopwatch": "stopwatch",
    "meter": "meter", "desktop": "device-desktop", "cloud": "cloud", "dot-fill": "dot-fill",
    "sort-asc": "sort-asc", "sort-desc": "sort-desc", "broadcast": "broadcast", "webhook": "webhook",
    "bug": "bug", "beaker": "beaker", "home": "home", "triangle-down": "triangle-down",
    "ellipsis": "ellipsis", "question": "question", "checklist": "checklist", "table": "table",
    "agent": "agent", "git-branch": "git-branch", "graph": "graph", "project": "project",
    "organization": "organization", "repo-push": "repo-push", "repo-pull": "repo-pull",
    "verified": "verified", "filter": "filter", "stack": "stack", "versions": "versions",
}

HEADER = """/* Иконки Octicons (16px), MIT License, Copyright (c) GitHub Inc. https://github.com/primer/octicons
   Файл сгенерирован tools/gen_icons.py - не редактируйте вручную. */
"""

FOOTER = """
  window.SCC_ICONS = I;
  /* Добавить/заменить иконку из модуля: SCC_ICONS['my-icon'] = '<path d="..."/>' (viewBox 16x16, fill=currentColor) */
  window.sccIcon = function (name, size, cls) {
    var body = I[name] || I.info;
    var s = size || 16;
    var span = document.createElement('span');
    span.innerHTML = '<svg class="octicon ' + (cls || '') + '" width="' + s + '" height="' + s +
      '" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">' + body + '</svg>';
    return span.firstChild;
  };
})();
"""


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = pathlib.Path(sys.argv[1])
    out = pathlib.Path(__file__).resolve().parent.parent / "scc" / "web" / "icons.js"
    icons, missing = {}, []
    for ours, theirs in sorted(ALIASES.items()):
        f = src / (theirs + "-16.svg")
        if not f.is_file():
            missing.append(theirs)
            continue
        m = re.search(r"<svg[^>]*>(.*)</svg>", f.read_text(encoding="utf-8"), re.S)
        icons[ours] = m.group(1).strip()
    lines = ["  var I = {"]
    for k, v in icons.items():
        lines.append("    %s: %s," % (json.dumps(k), json.dumps(v)))
    lines.append("  };")
    out.write_text(HEADER + "(function () {\n" + "\n".join(lines) + "\n" + FOOTER, encoding="utf-8")
    print("Записано %d иконок -> %s" % (len(icons), out))
    if missing:
        print("Не найдены:", ", ".join(missing))
        sys.exit(1)


if __name__ == "__main__":
    main()
