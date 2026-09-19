$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolRoot = Join-Path $root 'ai4s-tool'
$javaHome = 'E:\software\Inelij Idea\IntelliJ IDEA 2025.2.5\jbr'
$uv = Join-Path $root 'data\reproduction\bootstrap\bin\uv.exe'
$jar = Join-Path $root 'AI4S-agent-app\target\AI4S-agent-app.jar'

Set-Location $root

# Keep PowerShell and child Java processes on UTF-8 so Chinese log messages are
# rendered correctly in the Windows terminal.
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try { chcp 65001 | Out-Null } catch { }

# Keep package-manager caches and the Java runtime on the E: workspace.
$env:UV_CACHE_DIR = Join-Path $root '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.cache\uv-python'
$env:npm_config_cache = Join-Path $root '.cache\npm'
$env:PNPM_HOME = Join-Path $root '.pnpm-home'
$env:MAVEN_USER_HOME = Join-Path $root '.m2'
$env:JAVA_HOME = $javaHome
$env:Path = "$javaHome\bin;$env:Path"

function Get-DotEnvValue([string]$name) {
    $line = Get-Content (Join-Path $toolRoot '.env') -Encoding UTF8 |
        Where-Object { $_ -match "^$([regex]::Escape($name))=" } |
        Select-Object -Last 1
    if (-not $line) { return '' }
    return ($line -split '=', 2)[1].Trim()
}

if (-not (Test-Path (Join-Path $toolRoot '.env'))) {
    throw "Missing $toolRoot\.env"
}
if (-not (Test-Path $uv)) {
    throw "Missing uv executable: $uv"
}
if (-not (Test-Path $jar)) {
    throw "Missing backend jar: $jar"
}

$mysqlService = Get-Service -Name 'MySQL81', 'MySQL80', 'MySQL' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($mysqlService -and $mysqlService.Status -ne 'Running') {
    try {
        Start-Service -Name $mysqlService.Name
        Write-Host "Started MySQL service $($mysqlService.Name)."
    } catch {
        Write-Warning "Could not start MySQL service $($mysqlService.Name): $($_.Exception.Message)"
    }
}

$mysqlPassword = Get-DotEnvValue 'MYSQL_PASSWORD'
$mysqlUser = Get-DotEnvValue 'MYSQL_USER'
if ([string]::IsNullOrWhiteSpace($mysqlUser) -or $mysqlUser -eq 'ai4s') {
    $mysqlUser = 'root'
}
if (-not [string]::IsNullOrWhiteSpace($mysqlPassword)) {
    $env:SPRING_DATASOURCE_MYSQL_USERNAME = $mysqlUser
    $env:SPRING_DATASOURCE_MYSQL_PASSWORD = $mysqlPassword
    $env:SPRING_DATASOURCE_QUERY_USERNAME = $mysqlUser
    $env:SPRING_DATASOURCE_QUERY_PASSWORD = $mysqlPassword
}

$toolPort = Get-NetTCPConnection -LocalPort 1601 -State Listen -ErrorAction SilentlyContinue
function Test-StrategicMapWriteRoute {
    try {
        $openApi = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:1601/openapi.json').Content | ConvertFrom-Json
        $route = $openApi.paths.PSObject.Properties['/v1/strategic-map/teams/{team_id}']
        return $null -ne $route -and ($route.Value.PSObject.Properties.Name -contains 'put')
    } catch {
        return $false
    }
}

$hasStrategicMapWriteRoute = if ($toolPort) { Test-StrategicMapWriteRoute } else { $false }
if (-not $toolPort -or -not $hasStrategicMapWriteRoute) {
    if ($toolPort -and -not $hasStrategicMapWriteRoute) {
        Write-Host "Existing ai4s-tool process is missing the strategic-map save route; restarting PID $($toolPort.OwningProcess)."
        Stop-Process -Id $toolPort.OwningProcess -Force
        Start-Sleep -Milliseconds 500
    }
    Start-Process powershell.exe -WorkingDirectory $toolRoot -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $toolRoot 'start.ps1')
    ) | Out-Null
} else {
    Write-Host "ai4s-tool already listens on 1601 (PID $($toolPort.OwningProcess))."
}

$javaPort = Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue
if (-not $javaPort) {
    Start-Process (Join-Path $javaHome 'bin\java.exe') -WorkingDirectory $root -NoNewWindow -ArgumentList @(
        '-Dfile.encoding=UTF-8',
        '-Dsun.stdout.encoding=UTF-8',
        '-Dsun.stderr.encoding=UTF-8',
        '-Dstdout.encoding=UTF-8',
        '-Dstderr.encoding=UTF-8',
        '-jar', $jar, '--spring.profiles.active=prod'
    ) | Out-Null
} else {
    Write-Host "Java backend already listens on 8100 (PID $($javaPort.OwningProcess))."
}

Write-Host 'Backend startup requested. Health checks:'
Write-Host '  http://127.0.0.1:1601/docs'
Write-Host '  http://127.0.0.1:8100/web/health'
