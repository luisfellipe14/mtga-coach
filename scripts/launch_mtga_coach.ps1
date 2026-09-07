param([switch]$SmokeTest, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$entryPoint = Join-Path $projectRoot 'run.py'
$pythonPath = (Get-Command python -ErrorAction Stop).Source
if ($SmokeTest) {
    & $pythonPath $entryPoint --smoke
    exit $LASTEXITCODE
}
$appUrl = 'http://127.0.0.1:18731'
$ready = $false
try {
    $health = Invoke-RestMethod -Uri "$appUrl/api/health" -TimeoutSec 2
    $ready = ($health.status -eq 'ok' -and $health.app -eq 'mtga-coach')
} catch {}
if (-not $ready) {
    $runtimePath = Join-Path $env:LOCALAPPDATA 'mtga-coach'
    New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
    $arguments = @('"' + $entryPoint + '"', '--import-current')
    Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath 'server.out.log') -RedirectStandardError (Join-Path $runtimePath 'server.err.log') | Out-Null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$appUrl/api/health" -TimeoutSec 2
            if ($health.status -eq 'ok' -and $health.app -eq 'mtga-coach') { $ready = $true; break }
        } catch {}
    }
}
if (-not $ready) {
    throw 'MTGA Coach did not start. See %LOCALAPPDATA%/mtga-coach/server.err.log.'
}
if (-not $NoBrowser) { Start-Process $appUrl }
Write-Output "MTGA Coach is available at $appUrl"
