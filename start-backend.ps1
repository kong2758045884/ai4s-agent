$ErrorActionPreference = 'Stop'

# AI4S 研判系统本地后端启动器。
# 本脚本只管理本项目自己的进程：MySQL Windows 服务、ai4s-tool FastAPI
# 和 AI4S-agent-app Spring Boot。不会按进程名批量终止 java/python/node。

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$toolRoot = Join-Path $root 'ai4s-tool'
$javaHome = 'E:\software\Inelij Idea\IntelliJ IDEA 2025.2.5\jbr'
$pythonExe = Join-Path $toolRoot '.venv\Scripts\python.exe'
$jar = Join-Path $root 'AI4S-agent-app\target\AI4S-agent-app.jar'
$mavenLocal = Join-Path $root '.m2'
$logRoot = Join-Path $root 'runtime\logs\backend'
$toolStdoutLog = Join-Path $logRoot 'ai4s-tool.stdout.log'
$toolStderrLog = Join-Path $logRoot 'ai4s-tool.stderr.log'
$javaStdoutLog = Join-Path $logRoot 'java.stdout.log'
$javaStderrLog = Join-Path $logRoot 'java.stderr.log'
$buildLog = Join-Path $logRoot 'maven-build.log'
$buildErrorLog = Join-Path $logRoot 'maven-build.error.log'

$toolPort = 1601
$javaPort = 8100
$readinessTimeoutSeconds = 180

Set-Location $root
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

# Keep PowerShell, Maven, Java and Python output in UTF-8.
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try { chcp 65001 | Out-Null } catch { }

# Keep tool caches and the Java runtime inside the workspace.
$env:UV_CACHE_DIR = Join-Path $root '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.cache\uv-python'
$env:npm_config_cache = Join-Path $root '.cache\npm'
$env:PNPM_HOME = Join-Path $root '.pnpm-home'
$env:MAVEN_USER_HOME = $mavenLocal
$env:JAVA_HOME = $javaHome
$env:Path = "$javaHome\bin;$env:Path"

function Get-DotEnvValue([string]$name) {
    $dotenv = Join-Path $toolRoot '.env'
    if (-not (Test-Path $dotenv)) { return '' }
    $line = Get-Content $dotenv -Encoding UTF8 |
        Where-Object { $_ -match "^\s*$([regex]::Escape($name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return '' }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Get-Listener([int]$port) {
    return Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-ProcessInfo([int]$processId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
}

function Test-ProcessAlive([int]$processId) {
    return $null -ne (Get-ProcessInfo $processId)
}

function Test-ProjectProcess([int]$processId, [string]$marker) {
    # A Uvicorn worker may run from uv's managed Python path. Follow its parent
    # chain so we can still identify it without touching unrelated Python apps.
    $needle = $marker.ToLowerInvariant()
    $seen = @{}
    $current = $processId
    for ($i = 0; $i -lt 12 -and $current -gt 0 -and -not $seen.ContainsKey($current); $i++) {
        $seen[$current] = $true
        $info = Get-ProcessInfo $current
        if (-not $info) { return $false }
        $text = ("{0} {1}" -f $info.ExecutablePath, $info.CommandLine).ToLowerInvariant()
        if ($text.Contains($needle)) { return $true }
        $current = [int]$info.ParentProcessId
    }
    return $false
}

function Get-ProjectRootPid([int]$processId, [string]$marker) {
    $needle = $marker.ToLowerInvariant()
    $seen = @{}
    $rootPid = $processId
    $current = $processId
    for ($i = 0; $i -lt 12 -and $current -gt 0 -and -not $seen.ContainsKey($current); $i++) {
        $seen[$current] = $true
        $info = Get-ProcessInfo $current
        if (-not $info) { break }
        $text = ("{0} {1}" -f $info.ExecutablePath, $info.CommandLine).ToLowerInvariant()
        if (-not $text.Contains($needle)) { break }
        $rootPid = $current
        $current = [int]$info.ParentProcessId
    }
    return $rootPid
}

function Stop-ProcessTree([int]$processId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

function Show-LogTail([string[]]$paths) {
    foreach ($path in $paths) {
        if (Test-Path $path) {
            Write-Host "--- $path (tail) ---" -ForegroundColor DarkGray
            Get-Content $path -Tail 35 -ErrorAction SilentlyContinue
        }
    }
}

function Fail-Service([string]$service, [string]$reason, [string[]]$logs = @()) {
    Write-Host "[FAIL] ${service}: $reason" -ForegroundColor Red
    if ($logs.Count -gt 0) {
        Write-Host "日志位置: $($logs -join ', ')" -ForegroundColor Yellow
        Show-LogTail $logs
    }
    throw "$service 未就绪。$reason"
}

function Wait-HttpReady([string]$service, [string]$url, [int]$timeoutSeconds, [scriptblock]$validator = $null) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $lastError = '尚未收到 HTTP 响应'
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri $url
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                $valid = $true
                if ($null -ne $validator) {
                    $valid = [bool](& $validator $response)
                }
                if ($valid) {
                    return [pscustomobject]@{ Ready = $true; LastError = '' }
                }
                $lastError = "HTTP $($response.StatusCode)，响应内容不符合 $service readiness 约定"
            } else {
                $lastError = "HTTP $($response.StatusCode)"
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }
    return [pscustomobject]@{ Ready = $false; LastError = $lastError }
}

function Test-ToolOpenApi([int]$port) {
    try {
        $openApi = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:$port/openapi.json").Content | ConvertFrom-Json
        $paths = @($openApi.paths.PSObject.Properties.Name)
        $codeExecution = $openApi.paths.'/v1/tool/code_execution'.post.operationId
        return ($paths -contains '/v1/strategic-map/teams/{team_id}') -and
            ($paths -contains '/v1/file_tool/get_file') -and
            ($codeExecution -eq 'post_code_execution_v1_tool_code_execution_post')
    } catch {
        return $false
    }
}

function Wait-ToolEmbeddingReady([int]$port, [int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $lastError = '尚未收到 embedding HTTP 响应'
    $body = '{"inputs":["health_check"],"normalize":true}'
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 -Method Post -ContentType 'application/json' -Body $body -Uri "http://127.0.0.1:$port/v1/tool/embedding/text"
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                $payload = $response.Content | ConvertFrom-Json
                if (@($payload.vectors).Count -gt 0 -and [int]$payload.dimension -gt 0) {
                    return [pscustomobject]@{ Ready = $true; LastError = '' }
                }
                $lastError = 'HTTP 2xx，但响应没有有效向量'
            } else {
                $lastError = "HTTP $($response.StatusCode)"
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }
    return [pscustomobject]@{ Ready = $false; LastError = $lastError }
}

function Test-MySqlConnection([string]$dbHost, [int]$port, [string]$database, [string]$user, [string]$password) {
    $mysql = Get-Command mysql.exe -ErrorAction SilentlyContinue
    if (-not $mysql) {
        Write-Warning '未找到 mysql.exe，只能继续使用 TCP 和 Java JDBC readiness 检查。'
        return $true
    }
    $oldMysqlPwd = $env:MYSQL_PWD
    try {
        if ($password) { $env:MYSQL_PWD = $password } else { Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue }
        & $mysql.Source '--protocol=TCP' "--host=$dbHost" "--port=$port" "--user=$user" "--database=$database" '-e' 'SELECT 1' 1>$null 2>$null
        return ($LASTEXITCODE -eq 0)
    } finally {
        if ($null -eq $oldMysqlPwd) { Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue } else { $env:MYSQL_PWD = $oldMysqlPwd }
    }
}

function Ensure-MySql([string]$dbHost, [int]$port, [string]$database, [string]$user, [string]$password) {
    $listener = Get-Listener $port
    if (-not $listener) {
        $services = @(Get-Service -Name 'MySQL81', 'MySQL80', 'MySQL' -ErrorAction SilentlyContinue)
        $running = $services | Where-Object Status -eq 'Running' | Select-Object -First 1
        if (-not $running) {
            $started = $false
            $errors = @()
            foreach ($candidate in ($services | Where-Object Status -ne 'Running')) {
                try {
                    Start-Service -Name $candidate.Name -ErrorAction Stop
                    $started = $true
                    Write-Host "Started MySQL service $($candidate.Name)."
                    break
                } catch {
                    $errors += "$($candidate.Name): $($_.Exception.Message)"
                }
            }
            if (-not $started -and $services.Count -gt 0) {
                Fail-Service 'MySQL' ("没有可用的 MySQL 服务；启动尝试失败: " + ($errors -join ' | '))
            }
        } else {
            Write-Host "MySQL service $($running.Name) is already running."
        }

        $deadline = (Get-Date).AddSeconds(30)
        while (-not (Get-Listener $port) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 1
        }
    }

    if (-not (Get-Listener $port)) {
        Fail-Service 'MySQL' "端口 $port 未监听"
    }
    if (-not (Test-MySqlConnection $dbHost $port $database $user $password)) {
        Fail-Service 'MySQL' "TCP 已监听，但无法使用 $user@$database 建立真实 SQL 连接"
    }
    Write-Host "[PASS] MySQL $dbHost`:$port / database=$database"
}

if (-not (Test-Path (Join-Path $toolRoot '.env'))) {
    Fail-Service 'ai4s-tool 配置' "缺少 $toolRoot\.env"
}
if (-not (Test-Path $pythonExe)) {
    Fail-Service 'ai4s-tool Python' "缺少虚拟环境 $pythonExe，请先在 ai4s-tool 执行 uv sync"
}
if (-not (Test-Path (Join-Path $javaHome 'bin\java.exe'))) {
    Fail-Service 'Java' "缺少 Java 21 runtime: $javaHome"
}

$mysqlHost = Get-DotEnvValue 'MYSQL_HOST'
if ([string]::IsNullOrWhiteSpace($mysqlHost) -or $mysqlHost -eq 'mysql') { $mysqlHost = '127.0.0.1' }
$mysqlPortText = Get-DotEnvValue 'MYSQL_PORT'
$mysqlPortValue = 3306
if (-not [int]::TryParse($mysqlPortText, [ref]$mysqlPortValue)) { $mysqlPortValue = 3306 }
$mysqlDatabase = Get-DotEnvValue 'MYSQL_DATABASE'
if ([string]::IsNullOrWhiteSpace($mysqlDatabase)) { $mysqlDatabase = 'ai-agent-station' }
$mysqlUser = Get-DotEnvValue 'MYSQL_USER'
$mysqlPassword = Get-DotEnvValue 'MYSQL_PASSWORD'
$mysqlRootPassword = Get-DotEnvValue 'MYSQL_ROOT_PASSWORD'
if (($mysqlPassword -match '^replace-with-') -and $mysqlRootPassword -and $mysqlRootPassword -notmatch '^replace-with-') {
    $mysqlUser = 'root'
    $mysqlPassword = $mysqlRootPassword
}
if ([string]::IsNullOrWhiteSpace($mysqlUser)) { $mysqlUser = 'root' }

Ensure-MySql $mysqlHost $mysqlPortValue $mysqlDatabase $mysqlUser $mysqlPassword

# Build the executable automatically when it is missing or older than source.
$buildRequired = -not (Test-Path $jar)
if (-not $buildRequired) {
    $jarTime = (Get-Item $jar).LastWriteTime
    $buildInputs = @((Get-Item (Join-Path $root 'pom.xml')))
    $moduleDirs = Get-ChildItem $root -Directory -Filter 'AI4S-agent-*' -ErrorAction SilentlyContinue
    foreach ($module in $moduleDirs) {
        $buildInputs += Get-ChildItem $module.FullName -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '\\target\\' -and $_.Extension -in @('.java', '.xml', '.yml', '.yaml', '.properties', '.sql') }
    }
    $latestInput = $buildInputs | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $buildRequired = $null -ne $latestInput -and $latestInput.LastWriteTime -gt $jarTime
}

if ($buildRequired) {
    Write-Host 'Java JAR 不存在或已过期，开始构建 AI4S-agent-app（首次可能需要约 1-2 分钟）...'
    Remove-Item $buildLog, $buildErrorLog -Force -ErrorAction SilentlyContinue
    $mvn = Get-Command mvn.cmd -ErrorAction SilentlyContinue
    if (-not $mvn) { $mvn = Get-Command mvn -ErrorAction SilentlyContinue }
    if (-not $mvn) { Fail-Service 'Java 构建' '找不到 Maven（mvn.cmd）' @($buildLog) }
    $buildProcess = Start-Process -FilePath $mvn.Source -WorkingDirectory $root -ArgumentList @(
        '-pl', 'AI4S-agent-app', '-am', 'package', '-Dmaven.test.skip=true'
    ) -RedirectStandardOutput $buildLog -RedirectStandardError $buildErrorLog -WindowStyle Hidden -Wait -PassThru
    if ($buildProcess.ExitCode -ne 0 -or -not (Test-Path $jar)) {
        Fail-Service 'Java 构建' "Maven 退出码 $($buildProcess.ExitCode)" @($buildLog, $buildErrorLog)
    }
    Write-Host "[PASS] Java 构建完成: $jar"
} else {
    Write-Host "Java JAR 已存在且未过期: $jar"
}

# Python tool runtime: role=all keeps file, MRAG, strategic-map and sandbox
# routes in the one local process expected by the Vite proxy.
$env:ENV = 'prod'
$env:PYTHONIOENCODING = 'utf-8'
$env:AI4S_TOOL_ROLE = 'all'
$env:AI4S_TOOL_HOST = '127.0.0.1'
$env:AI4S_TOOL_PORT = "$toolPort"
$env:AI4S_TOOL_WORKERS = '1'
$env:SKILL_PYTHON_BIN = $pythonExe
$env:AI4S_SANDBOX_URL = "http://127.0.0.1:1602"
$env:LOG_PATH = Join-Path $toolRoot 'logs\server.log'
$textEmbeddingBaseUrl = Get-DotEnvValue 'TEXT_EMBEDDING_BASE_URL'
if ([string]::IsNullOrWhiteSpace($textEmbeddingBaseUrl)) {
    $textEmbeddingBaseUrl = Get-DotEnvValue 'OPENAI_BASE_URL'
}
$textEmbeddingApiKey = Get-DotEnvValue 'TEXT_EMBEDDING_API_KEY'
if ([string]::IsNullOrWhiteSpace($textEmbeddingApiKey)) {
    $textEmbeddingApiKey = Get-DotEnvValue 'OPENAI_API_KEY'
}
if ([string]::IsNullOrWhiteSpace($textEmbeddingApiKey)) {
    $textEmbeddingApiKey = Get-DotEnvValue 'DASHSCOPE_API_KEY'
}
if (-not [string]::IsNullOrWhiteSpace($textEmbeddingBaseUrl)) {
    $env:TEXT_EMBEDDING_BASE_URL = $textEmbeddingBaseUrl
}
if (-not [string]::IsNullOrWhiteSpace($textEmbeddingApiKey)) {
    $env:TEXT_EMBEDDING_API_KEY = $textEmbeddingApiKey
}
$fileSavePath = Get-DotEnvValue 'FILE_SAVE_PATH'
if ([string]::IsNullOrWhiteSpace($fileSavePath)) { $fileSavePath = Join-Path $toolRoot 'skilloutput' }
if (-not [System.IO.Path]::IsPathRooted($fileSavePath)) { $fileSavePath = Join-Path $toolRoot $fileSavePath }
$env:FILE_SAVE_PATH = $fileSavePath
New-Item -ItemType Directory -Force -Path $fileSavePath | Out-Null
if ([string]::IsNullOrWhiteSpace((Get-DotEnvValue 'FILE_SERVER_URL'))) {
    $env:FILE_SERVER_URL = "http://127.0.0.1:$toolPort/v1/file_tool"
}

$toolListener = Get-Listener $toolPort
if ($toolListener) {
    $toolPid = [int]$toolListener.OwningProcess
    # Some Windows Python launchers expose an empty Win32_Process.CommandLine
    # even for the project process. Validate the actual application routes first;
    # a matching ai4s-tool OpenAPI is stronger evidence than process metadata.
    $toolOpenApiMatches = Test-ToolOpenApi $toolPort
    if ($toolOpenApiMatches) {
        Write-Host "ai4s-tool 已在 $toolPort 就绪（PID $toolPid）。"
    } elseif (-not (Test-ProjectProcess $toolPid $toolRoot)) {
        $info = Get-ProcessInfo $toolPid
        Fail-Service 'ai4s-tool' "端口 $toolPort 已被其他进程占用（PID $toolPid，$($info.CommandLine)）"
    } else {
        # An API-role process exposes a proxy at the same URL but needs a
        # second sandbox process on 1602. Local development deliberately uses
        # role=all, so restart only this project's process when the role is
        # different or the loaded source is stale.
        Write-Host "发现本项目 ai4s-tool 进程但不是完整 all 角色，重启 PID $toolPid。"
        $toolRootPid = if (Test-ProjectProcess $toolPid $toolRoot) { Get-ProjectRootPid $toolPid $toolRoot } else { $toolPid }
        Stop-ProcessTree $toolRootPid
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Listener $toolPort) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
        if (Get-Listener $toolPort) {
            Fail-Service 'ai4s-tool' "旧项目进程无法退出，端口 $toolPort 仍被占用"
        }
        $toolProcess = Start-Process -FilePath $pythonExe -WorkingDirectory $toolRoot -ArgumentList @(
            'server.py', '--host', '127.0.0.1', '--port', "$toolPort", '--workers', '1', '--role', 'all'
        ) -RedirectStandardOutput $toolStdoutLog -RedirectStandardError $toolStderrLog -WindowStyle Hidden -PassThru
        Write-Host "Started ai4s-tool (PID $($toolProcess.Id), WorkingDirectory $toolRoot)."
    }
} else {
    Remove-Item $toolStdoutLog, $toolStderrLog -Force -ErrorAction SilentlyContinue
    $toolProcess = Start-Process -FilePath $pythonExe -WorkingDirectory $toolRoot -ArgumentList @(
        'server.py', '--host', '127.0.0.1', '--port', "$toolPort", '--workers', '1', '--role', 'all'
    ) -RedirectStandardOutput $toolStdoutLog -RedirectStandardError $toolStderrLog -WindowStyle Hidden -PassThru
    Write-Host "Started ai4s-tool (PID $($toolProcess.Id), WorkingDirectory $toolRoot)."
}

$toolOpenApiReady = Wait-HttpReady 'ai4s-tool OpenAPI' "http://127.0.0.1:$toolPort/openapi.json" $readinessTimeoutSeconds {
    param($response)
    try {
        $openApi = $response.Content | ConvertFrom-Json
        $paths = @($openApi.paths.PSObject.Properties.Name)
        $codeExecution = $openApi.paths.'/v1/tool/code_execution'.post.operationId
        return ($paths -contains '/v1/strategic-map/teams/{team_id}') -and
            ($paths -contains '/v1/file_tool/get_file') -and
            ($codeExecution -eq 'post_code_execution_v1_tool_code_execution_post')
    } catch { return $false }
}
if (-not $toolOpenApiReady.Ready) {
    Fail-Service 'ai4s-tool' "OpenAPI readiness 超时: $($toolOpenApiReady.LastError)" @($toolStdoutLog, $toolStderrLog, (Join-Path $toolRoot 'logs\server.log'))
}

# This is a real application request, not merely a TCP/port check. It proves
# the strategic-map router, SQLite store and response serialization are usable.
$toolApiReady = Wait-HttpReady 'ai4s-tool strategic-map API' "http://127.0.0.1:$toolPort/v1/strategic-map" 30 {
    param($response)
    try {
        $payload = $response.Content | ConvertFrom-Json
        return ([int]$payload.code -eq 200) -and $null -ne $payload.data
    } catch { return $false }
}
if (-not $toolApiReady.Ready) {
    Fail-Service 'ai4s-tool' "真实战略地图 API 未就绪: $($toolApiReady.LastError)" @($toolStdoutLog, $toolStderrLog, (Join-Path $toolRoot 'logs\server.log'))
}
Write-Host "[PASS] ai4s-tool HTTP API http://127.0.0.1:$toolPort/v1/strategic-map"

# MRAG's OpenAI-compatible embedding client has its own environment variable
# names. The fallback above makes the local .env usable without duplicating a
# working DashScope URL and key; exercise the real endpoint before declaring
# the Python backend ready.
$embeddingReady = Wait-ToolEmbeddingReady $toolPort 45
if (-not $embeddingReady.Ready -and $toolListener -and (-not $toolOpenApiMatches) -and (Test-ProjectProcess $toolPid $toolRoot)) {
    # A server started by an older copy of this script may still have the
    # broken empty TEXT_EMBEDDING_* environment. Restart only this project's
    # process once so the fallback values above take effect.
    Write-Host "ai4s-tool embedding readiness failed; restarting the project process to apply current embedding configuration."
    $toolRootPid = if (Test-ProjectProcess $toolPid $toolRoot) { Get-ProjectRootPid $toolPid $toolRoot } else { $toolPid }
    Stop-ProcessTree $toolRootPid
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Listener $toolPort) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
    if (Get-Listener $toolPort) {
        Fail-Service 'ai4s-tool' "旧项目进程无法退出，端口 $toolPort 仍被占用" @($toolStdoutLog, $toolStderrLog)
    }
    Remove-Item $toolStdoutLog, $toolStderrLog -Force -ErrorAction SilentlyContinue
    $toolProcess = Start-Process -FilePath $pythonExe -WorkingDirectory $toolRoot -ArgumentList @(
        'server.py', '--host', '127.0.0.1', '--port', "$toolPort", '--workers', '1', '--role', 'all'
    ) -RedirectStandardOutput $toolStdoutLog -RedirectStandardError $toolStderrLog -WindowStyle Hidden -PassThru
    Write-Host "Restarted ai4s-tool (PID $($toolProcess.Id), WorkingDirectory $toolRoot)."
    $embeddingReady = Wait-ToolEmbeddingReady $toolPort 45
}
if (-not $embeddingReady.Ready) {
    if ($toolOpenApiMatches) {
        # An already-running tool process may have been created by an older
        # shell with empty TEXT_EMBEDDING_* variables. Its core routes are
        # healthy; embedding depends on the optional external provider and is
        # reported without blocking Java/tool startup.
        Write-Host "[WARN] ai4s-tool embedding API 当前不可用: $($embeddingReady.LastError)" -ForegroundColor Yellow
        Write-Host "       这是可选外部向量服务；核心 Tool/文件/战略地图/代码执行路由仍继续检查。" -ForegroundColor Yellow
    } else {
        Fail-Service 'ai4s-tool embedding API' "真实 embedding API 未就绪: $($embeddingReady.LastError)" @($toolStdoutLog, $toolStderrLog, (Join-Path $toolRoot 'logs\server.log'))
    }
} else {
    Write-Host "[PASS] ai4s-tool embedding API http://127.0.0.1:$toolPort/v1/tool/embedding/text"
}

# Spring Boot local development overrides. The production YAML is still used
# for its complete datasource/tool configuration, but HTTP localhost must not
# issue a Secure visitor cookie that browsers will silently discard.
$env:SPRING_PROFILES_ACTIVE = 'prod'
$env:PUBLIC_ORIGIN = 'http://localhost:3000'
$env:AUTOBOTS_EXECUTION_VISITOR_COOKIE_SECURE = 'false'
$env:WORKSPACE_ROOT = $fileSavePath
$env:SKILL_DIR = Join-Path $root 'runtime\skills'
$env:AI4S_TOOL_BASE_URL = "http://127.0.0.1:$toolPort"
$env:SPRING_DATASOURCE_MYSQL_USERNAME = $mysqlUser
$env:SPRING_DATASOURCE_MYSQL_PASSWORD = $mysqlPassword
$env:SPRING_DATASOURCE_QUERY_USERNAME = $mysqlUser
$env:SPRING_DATASOURCE_QUERY_PASSWORD = $mysqlPassword
# application-prod.yml owns the complete JDBC URL. Only credentials are
# injected here; putting the ampersand-rich URL in a Windows environment
# variable makes Spring's relaxed binding split it on some JDK/PowerShell
# combinations and produces a misleading MySQL "identifier too long" error.

$javaListener = Get-Listener $javaPort
if ($javaListener) {
    $javaPid = [int]$javaListener.OwningProcess
    if (-not (Test-ProjectProcess $javaPid $jar)) {
        $info = Get-ProcessInfo $javaPid
        Fail-Service 'Java backend' "端口 $javaPort 已被其他进程占用（PID $javaPid，$($info.CommandLine)）"
    }
    Write-Host "Java backend 已监听 $javaPort（PID $javaPid），等待 HTTP readiness。"
} else {
    Remove-Item $javaStdoutLog, $javaStderrLog -Force -ErrorAction SilentlyContinue
    $javaProcess = Start-Process -FilePath (Join-Path $javaHome 'bin\java.exe') -WorkingDirectory $root -ArgumentList @(
        '-Dfile.encoding=UTF-8',
        '-Dsun.stdout.encoding=UTF-8',
        '-Dsun.stderr.encoding=UTF-8',
        '-Dstdout.encoding=UTF-8',
        '-Dstderr.encoding=UTF-8',
        '-jar', $jar,
        '--spring.profiles.active=prod'
    ) -RedirectStandardOutput $javaStdoutLog -RedirectStandardError $javaStderrLog -WindowStyle Hidden -PassThru
    Write-Host "Started Java backend (PID $($javaProcess.Id), WorkingDirectory $root)."
}

$javaHealthReady = Wait-HttpReady 'Java /web/health' "http://127.0.0.1:$javaPort/web/health" $readinessTimeoutSeconds {
    param($response)
    return ($response.Content.Trim() -eq 'ok')
}
if (-not $javaHealthReady.Ready) {
    Fail-Service 'Java backend' "核心 /web/health 未就绪: $($javaHealthReady.LastError)" @($javaStdoutLog, $javaStderrLog, (Join-Path $root 'data\log\log_error.log'), (Join-Path $root 'data\log\log_info.log'))
}

# Exercise a database-backed Java HTTP endpoint as a second readiness gate.
$javaApiReady = Wait-HttpReady 'Java visitor API' "http://127.0.0.1:$javaPort/api/agent/visitor/bootstrap" 30 {
    param($response)
    try {
        $payload = $response.Content | ConvertFrom-Json
        return ($response.StatusCode -eq 200) -and (($payload.code -eq '0000') -or ($payload.code -eq 200))
    } catch { return $false }
}
if (-not $javaApiReady.Ready) {
    Fail-Service 'Java backend' "数据库绑定的 visitor API 未就绪: $($javaApiReady.LastError)" @($javaStdoutLog, $javaStderrLog, (Join-Path $root 'data\log\log_error.log'), (Join-Path $root 'data\log\log_info.log'))
}
Write-Host "[PASS] Java HTTP API http://127.0.0.1:$javaPort/web/health"
Write-Host "[PASS] Java DB-backed API http://127.0.0.1:$javaPort/api/agent/visitor/bootstrap"

Write-Host ''
Write-Host 'Backend Ready' -ForegroundColor Green
Write-Host "  MySQL:     $mysqlHost`:$mysqlPortValue / $mysqlDatabase"
Write-Host "  ai4s-tool: http://127.0.0.1:$toolPort (all routes, including sandbox)"
Write-Host "  Java:      http://127.0.0.1:$javaPort"
Write-Host "  Logs:      $logRoot"
