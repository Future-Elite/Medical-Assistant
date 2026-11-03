param([ValidateRange(1,65535)][int]$Port = 8000, [switch]$SkipBuild, [switch]$Background)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $projectRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$pythonExe = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { Join-Path $projectRoot '.venv\Scripts\python.exe' } else { 'python' }
if (-not $SkipBuild) {
    if (-not (Test-Path -LiteralPath 'frontend\node_modules')) {
        & npm.cmd --cache "$projectRoot\tmp\npm-cache" --prefix frontend ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    & npm.cmd --cache "$projectRoot\tmp\npm-cache" --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
Write-Host "EHR Dreamer: http://127.0.0.1:$Port"
Write-Host "API docs: http://127.0.0.1:$Port/docs"
if ($Background) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
        throw 'Frontend build is missing. Run without -SkipBuild first.'
    }
    try {
        $existingHealth = Invoke-RestMethod "http://127.0.0.1:$Port/api/dreamer/health" -TimeoutSec 2
        if ($existingHealth.status -eq 'ok' -and $existingHealth.framework -eq 'EHR Dreamer') {
            Write-Host 'EHR Dreamer is already running; reusing the existing server.'
            return
        }
    } catch { }
    $runtimeDir = Join-Path $projectRoot 'outputs\runtime'
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $stdoutPath = Join-Path $runtimeDir "server$Port.stdout.log"
    $stderrPath = Join-Path $runtimeDir "server$Port.stderr.log"
    $resolvedPython = (Get-Command $pythonExe -ErrorAction Stop).Source
    $serverProcess = Start-Process -FilePath $resolvedPython -ArgumentList '-B','-m','uvicorn','ehrworld.web.app:app','--host','127.0.0.1','--port',"$Port" -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $deadline = (Get-Date).AddSeconds(15)
    $ready = $false
    do {
        if ($serverProcess.HasExited) { break }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/dreamer/health" -TimeoutSec 1
            $ready = $health.status -eq 'ok' -and $health.framework -eq 'EHR Dreamer'
        } catch { }
        if (-not $ready) { Start-Sleep -Milliseconds 250 }
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready -or $serverProcess.HasExited) {
        throw "Background server did not start. Check $stderrPath"
    }
    $serverProcess.Id | Set-Content -LiteralPath (Join-Path $runtimeDir "server$Port.pid")
    Write-Host "Background server ready (PID $($serverProcess.Id)). Closing this terminal will not intentionally stop it."
    Write-Host "Logs: $runtimeDir"
    return
}
& $pythonExe -m uvicorn ehrworld.web.app:app --host 127.0.0.1 --port $Port
