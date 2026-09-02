[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Run scripts\start-demo.ps1 once to create the local environment."
}

$env:PYTHONDONTWRITEBYTECODE = "1"
$verificationDatabase = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("arthaniyam-verify-" + [guid]::NewGuid().ToString() + ".sqlite3")
$env:ARTHANIYAM_DATABASE_PATH = $verificationDatabase

Push-Location $backendRoot
try {
    & $pythonExecutable -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) {
        throw "Backend verification failed."
    }
}
finally {
    Pop-Location
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($nodeCommand) {
    & $nodeCommand.Source --check (Join-Path $projectRoot "frontend\app.js")
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend JavaScript verification failed."
    }
}
else {
    Write-Warning "Node.js is unavailable; JavaScript syntax validation was skipped."
}

foreach ($suffix in @("", "-wal", "-shm")) {
    $databaseArtifact = "$verificationDatabase$suffix"
    if (Test-Path -LiteralPath $databaseArtifact) {
        Remove-Item -LiteralPath $databaseArtifact -Force
    }
}

Write-Host "ArthaNiyam verification passed." -ForegroundColor Green
