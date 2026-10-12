# 一键重启作文老师服务：按端口杀真实进程 -> 启动 -> 健康检查
$ErrorActionPreference = "Stop"
$listener = Get-NetTCPConnection -LocalPort 8778 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $listener | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Write-Host "ending old PID=$_"
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -WindowStyle Hidden -FilePath "py" -ArgumentList "my_agent\web_server.py" -WorkingDirectory $root
$ok = $false
foreach ($i in 1..15) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8778/api/stats" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if ($ok) { Write-Host "[OK] 服务已就绪" }
else { Write-Host "[失败] 未就绪，请检查 server_err.log"; exit 1 }
