$ErrorActionPreference = "Stop"

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $appDir "standardize_gui.py"
$codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path -LiteralPath $codexPython) {
    & $codexPython $scriptPath
    exit $LASTEXITCODE
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & py -3 $scriptPath
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python -and $python.Source -notlike "*\Microsoft\WindowsApps\python.exe") {
    & python $scriptPath
    exit $LASTEXITCODE
}

Write-Host "Python was not found."
Write-Host "Install Python 3.10 or newer, then run:"
Write-Host "python -m pip install -r requirements.txt"
Write-Host "python .\standardize_gui.py"
exit 1
