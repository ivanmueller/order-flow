# Opened by restore_from_usb.bat on the laptop, from the USB copy. Copies the project to -Target (default
# C:\order-flow), checks every file, builds .venv from requirements.txt and runs a quick test.
# Never deletes anything in the target; files from the USB overwrite older copies of the same file.
param([string]$Target = 'C:\order-flow', [switch]$SkipVenv)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'transfer_common.ps1')

$Src = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if ($Src.TrimEnd('\') -ieq $Target.TrimEnd('\')) { Write-Host 'Source and target are the same folder.' -ForegroundColor Red; exit 1 }
Write-Host "Restoring $Src -> $Target" -ForegroundColor Cyan
if (Test-Path -LiteralPath (Join-Path $Target 'config.yaml')) {
    Write-Host "$Target already holds a copy of the project. Files from the USB will overwrite the same files there; nothing is deleted." -ForegroundColor Yellow
    if ((Read-Host 'Continue? (y/N)') -notmatch '^[yY]') { exit 1 }
}

robocopy $Src $Target /E /XD @SkipDirs /XF *.pyc /R:2 /W:2 /MT:8 /FFT /NP /NFL /NDL /NJH
$Rc = $LASTEXITCODE
if ($Rc -ge 8) { Write-Host "robocopy reported errors (exit code $Rc). Run this again." -ForegroundColor Red; exit 1 }
$Check = Test-Copy -From $Src -To $Target
Write-CheckResult -Check $Check -Where $Target
if (-not $Check.Ok) { exit 1 }

if (-not (Test-Path -LiteralPath (Join-Path $Target '.env'))) {
    Write-Host ''
    Write-Host 'No .env here yet. Create it before any Databento call: copy .env.example to .env in' -ForegroundColor Yellow
    Write-Host "$Target and put your key on the line DATABENTO_API_KEY=... (from the Databento portal)." -ForegroundColor Yellow
}
if ($SkipVenv) { exit 0 }

# Python 3.11+ (the py launcher first, then python on PATH)
function Find-Python {
    foreach ($c in @(@{ Exe = 'py'; Args = @('-3') }, @{ Exe = 'python'; Args = @() })) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $ok = $null
        try { $ok = (& $c.Exe @($c.Args) -c 'import sys; print(int(sys.version_info >= (3, 11)))' 2>$null) } catch { }
        if ("$ok".Trim() -eq '1') { return $c }
    }
    return $null
}
$Py = Find-Python
if (-not $Py) {
    Write-Host ''
    Write-Host 'Python 3.11 or newer was not found. Install it from python.org (tick "Add python.exe to PATH"),' -ForegroundColor Red
    Write-Host 'then run restore_from_usb.bat again: the copy is already done and is only checked the second time.' -ForegroundColor Red
    exit 1
}
$Venv = Join-Path $Target '.venv'
$VPy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VPy)) {
    Write-Host 'Creating .venv ...'
    & $Py.Exe @($Py.Args) -m venv $Venv
}
Write-Host 'Installing packages from requirements.txt (a few minutes the first time) ...'
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install -r (Join-Path $Target 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed (see above). Check the internet connection and run again.' -ForegroundColor Red; exit 1 }

Write-Host 'Quick test (study 8 and study 10 tests) ...'
Push-Location -LiteralPath $Target
& $VPy -m pytest -q tests/test_study8.py tests/test_study10.py
$T = $LASTEXITCODE
Pop-Location
Write-Host ''
if ($T -eq 0) {
    Write-Host "Ready. Use open_venv.bat or open_venv_api.bat in $Target as before. The full suite is pytest -q (several minutes)." -ForegroundColor Green
    exit 0
}
Write-Host 'The quick test failed (see above). The files are in place; send me the output.' -ForegroundColor Red
exit 1
