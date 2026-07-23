$ErrorActionPreference = "Stop"

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolDir = Join-Path $appDir ".build_pyinstaller"
$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = "py"
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand -and $pythonCommand.Source -notlike "*\Microsoft\WindowsApps\python.exe") {
            $python = "python"
        } else {
            throw "Python 3 was not found."
        }
    }
}

if (-not (Test-Path -LiteralPath $toolDir)) {
    New-Item -ItemType Directory -Path $toolDir | Out-Null
}

$checkScript = @"
import sys
sys.path.insert(0, r'''$toolDir''')
for package in ('PyInstaller', 'pandas', 'openpyxl', 'xlrd', 'tkinterdnd2', 'PIL'):
    __import__(package)
"@

& $python -c $checkScript
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install --upgrade --target $toolDir -r (Join-Path $appDir "requirements.txt") pyinstaller
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:PYTHONPATH = $toolDir

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name StandardizeDataPlotter `
    --collect-all tkinterdnd2 `
    --distpath (Join-Path $appDir "dist") `
    --workpath (Join-Path $appDir "build") `
    --specpath $appDir `
    (Join-Path $appDir "standardize_gui.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built: $(Join-Path $appDir 'dist\StandardizeDataPlotter.exe')"
