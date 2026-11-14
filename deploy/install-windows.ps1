# Установка Server Control Web как автозапускаемой задачи Windows (Windows 10/11, от имени администратора).
#   powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
$ErrorActionPreference = "Stop"

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Запустите PowerShell от имени администратора."
}
$dir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python не найден. Устанавливаю через winget..."
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    Write-Host "Откройте новое окно PowerShell и запустите скрипт снова (чтобы обновился PATH)."
    exit 0
}

$name = "ServerControlWeb"
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$dir\run.bat`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$prin    = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$set     = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $prin -Settings $set -Force | Out-Null
Start-ScheduledTask -TaskName $name

$port = 8080
if (Test-Path "$dir\config.json") { try { $port = (Get-Content "$dir\config.json" -Raw | ConvertFrom-Json).port } catch {} ; if (-not $port) { $port = 8080 } }
New-NetFirewallRule -DisplayName "Server Control Web ($port)" -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -ErrorAction SilentlyContinue | Out-Null

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "Готово. Задача планировщика: $name (запуск при старте системы, от SYSTEM)."
Write-Host "Откройте в браузере: http://localhost:$port"
if (Test-Path "$dir\data\setup_token.txt") { Write-Host ("Код первоначальной настройки: " + (Get-Content "$dir\data\setup_token.txt")) }
