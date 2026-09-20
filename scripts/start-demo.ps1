[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [ValidateSet("reference", "openai")]
    [string]$InvestigatorMode = "reference"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$virtualEnvironment = Join-Path $projectRoot ".venv"
$pythonExecutable = Join-Path $virtualEnvironment "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.11 or newer is required and was not found on PATH."
    }
    & $pythonCommand.Source -m venv $virtualEnvironment
    & $pythonExecutable -m pip install --upgrade pip
    & $pythonExecutable -m pip install -e "${backendRoot}[dev]"
}

$env:RAZORPAY_MODE = "simulate"
$env:POLICY_COMPILER_MODE = "reference"
$env:SUPPORT_INVESTIGATOR_MODE = $InvestigatorMode
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "ArthaNiyam demo starting at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Support investigator: $InvestigatorMode; all payments remain simulated." -ForegroundColor DarkGray
Write-Host "Press Ctrl+C to stop the server." -ForegroundColor DarkGray

Push-Location $backendRoot
try {
    & $pythonExecutable -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
