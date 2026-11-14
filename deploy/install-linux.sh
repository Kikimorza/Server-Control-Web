#!/usr/bin/env bash
# Установка Server Control Web как systemd-службы (Ubuntu/Debian и другие дистрибутивы с systemd).
#   sudo ./deploy/install-linux.sh [каталог установки, по умолчанию /opt/server-control-web]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root: sudo $0"; exit 1; }
command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "Нужен Python 3.8+"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/opt/server-control-web}"

if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  cp -a "$SRC"/. "$DEST"/
fi
chmod +x "$DEST/run.sh" "$DEST/run.py"

sed "s|__DIR__|$DEST|g" "$DEST/deploy/scc.service" > /etc/systemd/system/server-control-web.service
systemctl daemon-reload
systemctl enable --now server-control-web.service

sleep 2
PORT="$(python3 -c "import json;print(json.load(open('$DEST/config.json')).get('port',8080))" 2>/dev/null || echo 8080)"
echo
echo "Готово. Служба: systemctl status server-control-web"
echo "Откройте в браузере:  http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
if [ -f "$DEST/data/setup_token.txt" ]; then
  echo "Код первоначальной настройки: $(cat "$DEST/data/setup_token.txt")"
fi
