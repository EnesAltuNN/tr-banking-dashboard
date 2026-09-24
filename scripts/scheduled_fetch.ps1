<#
Runs `tr-banking fetch` and appends its output to data\logs\fetch.log.
Called by the scheduled task from register_scheduled_fetch.ps1; can also be run by hand:
    powershell -ExecutionPolicy Bypass -File scripts\scheduled_fetch.ps1
#>
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "fetch.log"

# Scheduled tasks may not see the user's PATH, so fall back to uv's default install location.
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }

# Python writes UTF-8 and PowerShell decodes it as UTF-8, so Turkish characters survive.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

Set-Location $root
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') tr-banking fetch =====" |
    Add-Content -Path $log -Encoding UTF8
# In Windows PowerShell, 2>&1 wraps each stderr line in an error record; "$_" turns it back
# into plain text (our log output goes to stderr).
& $uv run tr-banking fetch 2>&1 | ForEach-Object { "$_" } | Add-Content -Path $log -Encoding UTF8
$exitCode = $LASTEXITCODE
"exit code: $exitCode" | Add-Content -Path $log -Encoding UTF8
exit $exitCode
