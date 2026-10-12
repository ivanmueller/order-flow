# Opened by open_venv.bat: go to the repo root and activate the virtual environment.
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$Activate = Join-Path $Repo '.venv\Scripts\Activate.ps1'
if (Test-Path $Activate) {
    . $Activate
    Write-Host "order-flow: $Repo  (venv active)" -ForegroundColor Green
} else {
    Write-Host "No .venv found at $Activate. Create it with: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt" -ForegroundColor Yellow
}
if ($env:GAMMA_EDGE_CONFIG) { Write-Host "Note: GAMMA_EDGE_CONFIG = $($env:GAMMA_EDGE_CONFIG)" -ForegroundColor Yellow }
