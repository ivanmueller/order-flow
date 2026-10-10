# Opened by open_venv_api.bat: venv session plus a check of the data APIs. Prints no keys.
. (Join-Path $PSScriptRoot 'venv_session.ps1')
$Repo = Split-Path -Parent $PSScriptRoot

# 1. Databento key: present in .env (or the shell)? The value is never printed.
$EnvFile = Join-Path $Repo '.env'
$HasKey = $env:DATABENTO_API_KEY -or ((Test-Path $EnvFile) -and (Select-String -Path $EnvFile -Pattern '^\s*(export\s+)?DATABENTO_API_KEY\s*=\s*\S' -Quiet))
if ($HasKey) { Write-Host "Databento key: found" -ForegroundColor Green }
else { Write-Host "Databento key: MISSING. Add a line DATABENTO_API_KEY=... to $EnvFile" -ForegroundColor Red }

# 2. Databento: a free metadata call (list_datasets costs nothing).
if ($HasKey) {
    python -c "from src.config import load_env_file; load_env_file(); from src import spend; spend.client().metadata.list_datasets(); print('Databento API: OK')"
    if ($LASTEXITCODE -ne 0) { Write-Host "Databento API: FAILED (see the error above)" -ForegroundColor Red }
}

# 3. ThetaData Terminal (only for option quotes): already running? If not, start it when a jar is found.
function Test-Theta { try { Invoke-WebRequest -Uri 'http://127.0.0.1:25503/v3' -TimeoutSec 3 -UseBasicParsing | Out-Null; $true } catch { $_.Exception.Response -ne $null } }
if (Test-Theta) { Write-Host "ThetaData Terminal: running on 127.0.0.1:25503" -ForegroundColor Green }
else {
    $Jar = if ($env:THETA_JAR) { $env:THETA_JAR } else { Get-ChildItem -Path $Repo, (Join-Path $env:USERPROFILE 'ThetaData') -Filter 'ThetaTerminal*.jar' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }
    if ($Jar) {
        Write-Host "Starting ThetaData Terminal: $Jar (new window)" -ForegroundColor Yellow
        Start-Process java -ArgumentList '-jar', "`"$Jar`"" -WorkingDirectory (Split-Path -Parent $Jar)
        Start-Sleep -Seconds 15
        if (Test-Theta) { Write-Host "ThetaData Terminal: running" -ForegroundColor Green }
        else { Write-Host "ThetaData Terminal: not answering yet; check its window (login may be needed)" -ForegroundColor Yellow }
    } else {
        Write-Host "ThetaData Terminal: not running and no ThetaTerminal*.jar found. Only needed for option quotes; set THETA_JAR to the jar path to auto-start it." -ForegroundColor Yellow
    }
}
Write-Host "Ready. Spend rule: price first (--price-only), then --approve-usd." -ForegroundColor Cyan
