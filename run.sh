#!/usr/bin/env bash
# Запуск с автоперезапуском (код 3 = перезапуск после обновления/по запросу).
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { echo "Python 3.8+ не найден (sudo apt install python3)"; exit 1; }
export SCC_SUPERVISED=1
while true; do
  "$PY" run.py "$@"
  code=$?
  [ "$code" -eq 3 ] || exit "$code"
  echo "Перезапуск..."; sleep 1
done
