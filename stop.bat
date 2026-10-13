@echo off
rem Essay Teacher - one-click stop (kill real process by port)
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8778 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host ('stopped PID=' + $_) }"
