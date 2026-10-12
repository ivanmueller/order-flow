# Opened by backup_to_usb.bat. Copies the whole project to <Drive>:\order-flow so work can continue on another
# computer: code, docs, configs, git history, every data folder (data, data_nq, data_cl, data_gc, data_zn,
# data_6e) and the Databento spend ledger (data\spend_ledger.csv). Skips .venv and caches (rebuilt on the laptop).
# .env holds your Databento API key: it is copied only if you say yes (or pass -IncludeEnv; -NoEnv never copies).
# Safe to run again: robocopy copies only new or changed files and never deletes anything.
param([string]$Drive = 'E:', [switch]$IncludeEnv, [switch]$NoEnv)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'transfer_common.ps1')

$Repo = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Letter = $Drive.Trim().TrimEnd('\').TrimEnd(':')
$DriveRoot = "$($Letter):\"
if (-not (Test-Path -LiteralPath $DriveRoot)) {
    Write-Host "Drive $($Letter): not found. Plug in the USB drive, or name its letter: backup_to_usb.bat F:" -ForegroundColor Red
    exit 1
}
$Dest = Join-Path $DriveRoot 'order-flow'
if ($Dest.TrimEnd('\') -ieq $Repo.TrimEnd('\')) { Write-Host 'Source and destination are the same folder.' -ForegroundColor Red; exit 1 }
Write-Host "Backing up $Repo -> $Dest" -ForegroundColor Cyan

$Commit = 'unknown (git not found)'
if (Get-Command git -ErrorAction SilentlyContinue) {
    try {   # git warnings on stderr must not stop the backup (Windows PowerShell 5.1 treats them as errors)
        $ErrorActionPreference = 'Continue'
        $Commit = "$(git -C $Repo rev-parse --short HEAD 2>$null)".Trim()
        $Dirty = @(git -C $Repo status --porcelain 2>$null | Where-Object { $_ -is [string] -and $_ }).Count
        Write-Host "git commit $Commit; files with uncommitted changes: $Dirty (they are copied as they are)"
    } catch { $Commit = 'unknown' } finally { $ErrorActionPreference = 'Stop' }
}

# Size, free space, and the FAT32 4 GB file limit
$Files = @(Get-RepoFiles -Root $Repo -AlsoSkip @('.env'))
$Bytes = [long](($Files | Measure-Object Length -Sum).Sum)
$Existing = 0
if (Test-Path -LiteralPath $Dest) { $Existing = [long]((@(Get-RepoFiles -Root $Dest) | Measure-Object Length -Sum).Sum) }
$Needed = [math]::Max(0, $Bytes - $Existing)
$Free = (Get-PSDrive -Name $Letter).Free
Write-Host ("To copy: {0:N0} files, {1:N2} GB; free on {2}: {3:N2} GB" -f $Files.Count, ($Bytes / 1GB), $DriveRoot, ($Free / 1GB))
if ($Free -lt $Needed * 1.05) {
    Write-Host ("Not enough space on $($DriveRoot): about {0:N2} GB more is needed." -f (($Needed * 1.05 - $Free) / 1GB)) -ForegroundColor Red
    exit 1
}
$Fs = $null
try { $Fs = (Get-Volume -DriveLetter $Letter -ErrorAction Stop).FileSystem } catch { }
if ($Fs -eq 'FAT32') {
    $Big = @($Files | Where-Object { $_.Length -ge 4GB })
    if ($Big.Count) {
        Write-Host 'The USB drive is FAT32, which cannot hold files of 4 GB or more:' -ForegroundColor Red
        $Big | ForEach-Object { Write-Host "  $($_.Rel)" }
        Write-Host 'Reformat it as exFAT (back up anything on it first) and run this again.' -ForegroundColor Yellow
        exit 1
    }
}

# Copy (/E all subfolders, /FFT tolerant timestamps for FAT/exFAT, /MT:8 parallel, 2 retries)
robocopy $Repo $Dest /E /XD @SkipDirs /XF .env *.pyc /R:2 /W:2 /MT:8 /FFT /NP /NFL /NDL /NJH
$Rc = $LASTEXITCODE
if ($Rc -ge 8) { Write-Host "robocopy reported errors (exit code $Rc). Check the drive and run again." -ForegroundColor Red; exit 1 }

# .env (the API key)
$EnvSrc = Join-Path $Repo '.env'
$CopyEnv = $false
if ((Test-Path -LiteralPath $EnvSrc) -and -not $NoEnv) {
    if ($IncludeEnv) { $CopyEnv = $true }
    else {
        Write-Host ''
        Write-Host '.env holds your Databento API key. If the USB stick is lost, whoever finds it could spend your remaining credit.' -ForegroundColor Yellow
        Write-Host 'Alternative: say N, and on the laptop paste the key from the Databento portal into .env.' -ForegroundColor Yellow
        $CopyEnv = ((Read-Host 'Copy .env to the USB drive? (y/N)') -match '^[yY]')
    }
}
if ($CopyEnv) { Copy-Item -LiteralPath $EnvSrc -Destination (Join-Path $Dest '.env') -Force; Write-Host '.env copied.' }
else { Write-Host '.env not copied.' }

# Check every file
$Skip = @()
if (-not $CopyEnv) { $Skip = @('.env') }
$Check = Test-Copy -From $Repo -To $Dest -AlsoSkip $Skip
Write-CheckResult -Check $Check -Where $Dest
$Ledger = Join-Path $Dest 'data\spend_ledger.csv'
if (Test-Path -LiteralPath $Ledger) { Write-Host 'Spend ledger: copied (data\spend_ledger.csv).' -ForegroundColor Green }
else { Write-Host 'Spend ledger: not found in data\ (the laptop would start the spend count at zero).' -ForegroundColor Yellow }

$Note = @(
    "order-flow transfer copy"
    "made:      $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    "from:      $Repo"
    "git:       $Commit"
    ("files:     {0:N0}, {1:N2} GB, check {2}" -f $Check.Files, ($Check.Bytes / 1GB), $(if ($Check.Ok) { 'PASSED' } else { 'FAILED' }))
    "api key:   $(if ($CopyEnv) { '.env copied' } else { '.env NOT copied: add DATABENTO_API_KEY=... to .env on the laptop' })"
    ""
    "On the laptop: install Python 3.11+ (python.org, tick 'Add python.exe to PATH') and Git, then double-click"
    "restore_from_usb.bat in this folder. It copies the project to C:\order-flow, checks every file, builds .venv"
    "and runs a quick test. Then use open_venv.bat / open_venv_api.bat in C:\order-flow as before."
)
$Note | Set-Content -LiteralPath (Join-Path $Dest 'TRANSFER_NOTE.txt') -Encoding UTF8
Write-Host ''
if ($Check.Ok) { Write-Host "Done. Eject the drive safely before unplugging it. Instructions are in $Dest\TRANSFER_NOTE.txt" -ForegroundColor Green; exit 0 }
exit 1
