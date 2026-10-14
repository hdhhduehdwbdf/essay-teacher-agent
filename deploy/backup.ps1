# Windows 计划任务用：语料 + SQLite 备份（保留最近 14 份）
$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$out = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { "D:\backups" }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$root = Split-Path -Parent $PSScriptRoot
Compress-Archive -Path "$root\knowledge" -DestinationPath "$out\knowledge_$ts.zip" -Force
if ($env:DATABASE_URL) { Write-Host "Postgres 环境请改用 deploy/backup.sh + pg_dump" }
elseif (Test-Path "$root\data\app.db") { Copy-Item "$root\data\app.db" "$out\db_$ts.db" }
Get-ChildItem $out | Sort-Object LastWriteTime -Descending | Select-Object -Skip 14 | Remove-Item -Force
Write-Host "backup done -> $out ($ts)"
