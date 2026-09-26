param([switch]$Restart)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolRoot = Join-Path $projectRoot 'ai4s-tool'
$previewRoot = Join-Path $projectRoot 'runtime/integration-preview-20260925'
$fusionRoot = Join-Path $projectRoot 'runtime/hyper-fusion-20260926'
foreach ($required in @('strategic_map.db', 'impact_triage.db')) {
    if (-not (Test-Path -LiteralPath (Join-Path $previewRoot $required))) { throw "Missing preview database: $required" }
}
if (-not (Test-Path -LiteralPath (Join-Path $fusionRoot 'graphs/manifest.json'))) { throw 'Missing reconciled graph snapshot' }
$listener = Get-NetTCPConnection -LocalPort 1604 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    if (-not $Restart) {
        $status = Invoke-RestMethod 'http://127.0.0.1:1604/v1/strategic-map/integration/status' -TimeoutSec 10
        Write-Output "Fusion API already running; $($status.triage.events) source events."
        exit 0
    }
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($serverProcess.CommandLine -notmatch 'server.py.*--port 1604') { throw 'Port 1604 belongs to another service' }
    $env:AI4S_PREVIEW_RESTART_DB = Join-Path $previewRoot 'strategic_map.db'
    & (Join-Path $toolRoot '.venv/Scripts/python.exe') -c 'import os,sqlite3; c=sqlite3.connect("file:"+os.environ["AI4S_PREVIEW_RESTART_DB"].replace(chr(92),"/")+"?mode=ro",uri=True); tables=["strategic_map_refresh_task","strategic_map_graph_scan_task"]; active=sum(c.execute("SELECT COUNT(*) FROM "+t+" WHERE state IN (?,?,?)",("running","accepted","queued")).fetchone()[0] for t in tables); assert not active, "Active research task; restart refused"'
    if ($LASTEXITCODE -ne 0) { throw 'Active jobs check failed; restart refused' }
    Stop-Process -Id $listener.OwningProcess
}
$env:ENV = 'preview'
$env:STRATEGIC_MAP_SCHEDULER_ENABLED = 'false'
$env:STRATEGIC_MAP_SKIP_STARTUP_SYNC = 'true'
$env:AI4S_SKIP_EMBEDDING_HEALTH = '1'
$env:STRATEGIC_MAP_DB_PATH = Join-Path $previewRoot 'strategic_map.db'
$env:AI4S_IMPACT_DB_PATH = Join-Path $previewRoot 'impact_triage.db'
$env:STRATEGIC_MAP_HYPER_SNAPSHOT_DIR = Join-Path $fusionRoot 'graphs'
$env:HYPEREXTRACT_BASE_URL = 'http://127.0.0.1:1606'
$env:PYTHONIOENCODING = 'utf-8'
$logDir = Join-Path $fusionRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$started = Start-Process -FilePath (Join-Path $toolRoot '.venv/Scripts/python.exe') -ArgumentList @('server.py', '--host', '127.0.0.1', '--port', '1604', '--workers', '1') -WorkingDirectory $toolRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "$stamp.stdout.log") -RedirectStandardError (Join-Path $logDir "$stamp.stderr.log")
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:1604/v1/strategic-map/integration/status' -TimeoutSec 1
        Write-Output "Fusion API ready. PID $($started.Id), $($status.triage.events) source events. UI: http://localhost:3000/workspace/strategic-map"
        exit 0
    } catch { if ($started.HasExited) { throw "Fusion API exited; see $logDir" } }
}
throw "Startup timeout; see $logDir"
