# Shared by backup_to_usb.ps1 and restore_from_usb.ps1: what a transfer skips, and a file-by-file check of a copy.
# Skipped: the virtual environment and caches (rebuilt on the other computer). Everything else travels:
# code, docs, configs, .git, every data folder and data\spend_ledger.csv.

$SkipDirs  = @('.venv', '__pycache__', '.pytest_cache', '.ipynb_checkpoints')
$SkipFiles = @('*.pyc')

function Get-RepoFiles {
    param([string]$Root, [string[]]$AlsoSkip = @())
    $rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $patterns = @($SkipFiles) + @($AlsoSkip)
    Get-ChildItem -LiteralPath $rootFull -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($rootFull.Length + 1)
        $parts = @($rel -split '[\\/]')
        $skip = $false
        if ($parts.Count -gt 1) {
            foreach ($p in $parts[0..($parts.Count - 2)]) { if ($SkipDirs -contains $p) { $skip = $true; break } }
        }
        if (-not $skip) { foreach ($pat in $patterns) { if ($rel -like $pat -or $_.Name -like $pat) { $skip = $true; break } } }
        if (-not $skip) { [pscustomobject]@{ Rel = $rel; Top = $parts[0]; IsDir = ($parts.Count -gt 1); Length = $_.Length } }
    }
}

function Test-Copy {
    # Every file under $From must exist under $To with the same size. Returns a summary object.
    param([string]$From, [string]$To, [string[]]$AlsoSkip = @())
    $src = @(Get-RepoFiles -Root $From -AlsoSkip $AlsoSkip)
    $dst = @{}
    foreach ($f in @(Get-RepoFiles -Root $To -AlsoSkip $AlsoSkip)) { $dst[$f.Rel.ToLowerInvariant()] = $f.Length }
    $missing = New-Object System.Collections.Generic.List[string]
    $different = New-Object System.Collections.Generic.List[string]
    foreach ($f in $src) {
        $k = $f.Rel.ToLowerInvariant()
        if (-not $dst.ContainsKey($k)) { $missing.Add($f.Rel) }
        elseif ($dst[$k] -ne $f.Length) { $different.Add($f.Rel) }
    }
    $data = $src | Where-Object { $_.IsDir -and $_.Top -like 'data*' } | Group-Object Top | Sort-Object Name | ForEach-Object {
        [pscustomobject]@{ Folder = $_.Name; Files = $_.Count
                           MB = [math]::Round((($_.Group | Measure-Object Length -Sum).Sum) / 1MB, 1) }
    }
    [pscustomobject]@{
        Files = $src.Count
        Bytes = [long](($src | Measure-Object Length -Sum).Sum)
        Missing = $missing
        Different = $different
        Data = $data
        Ok = ($missing.Count -eq 0 -and $different.Count -eq 0)
    }
}

function Write-CheckResult {
    param($Check, [string]$Where)
    Write-Host ''
    Write-Host 'Data folders copied:' -ForegroundColor Cyan
    if ($Check.Data) {
        foreach ($r in $Check.Data) { Write-Host ("  {0,-10} {1,8:N0} files {2,10:N1} MB" -f $r.Folder, $r.Files, $r.MB) }
    } else { Write-Host '  (none found)' -ForegroundColor Yellow }
    Write-Host ("Checked {0:N0} files, {1:N2} GB, file by file." -f $Check.Files, ($Check.Bytes / 1GB))
    if ($Check.Ok) {
        Write-Host "CHECK PASSED: every file is present in $Where with the same size." -ForegroundColor Green
    } else {
        Write-Host ("CHECK FAILED: {0} missing, {1} with a different size in {2}." -f $Check.Missing.Count, $Check.Different.Count, $Where) -ForegroundColor Red
        $Check.Missing | Select-Object -First 10 | ForEach-Object { Write-Host "  missing:   $_" -ForegroundColor Red }
        $Check.Different | Select-Object -First 10 | ForEach-Object { Write-Host "  different: $_" -ForegroundColor Red }
        Write-Host 'Run it again: only the missing or changed files are copied the second time.' -ForegroundColor Yellow
    }
}
