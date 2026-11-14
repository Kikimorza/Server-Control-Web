@echo off
rem Запуск с автоперезапуском (код 3 = перезапуск после обновления/по запросу).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set SCC_SUPERVISED=1
where python >nul 2>nul && (set PY=python) || (where py >nul 2>nul && (set PY=py -3) || (echo Python 3.8+ не найден. Установите: winget install Python.Python.3.12 & pause & exit /b 1))
:loop
%PY% run.py %*
if %errorlevel%==3 (
  echo Перезапуск...
  timeout /t 1 >nul
  goto loop
)
