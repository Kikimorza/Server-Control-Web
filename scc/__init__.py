"""Server Control Web - модульная панель управления серверами."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Server Control Web"


def _read_version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = _read_version()
