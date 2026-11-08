# Local fallback collector for Windows Task Scheduler.
#
# Runs the same jobs as .github/workflows/collect.yml on this PC, then commits
# the new data LOCALLY (it never pushes; publish with "Sync Changes" in VS Code
# or `git push`). Use it only while GitHub Actions is not running the
# workflow: if both run, the same hours get collected twice.
#
#   powershell -ExecutionPolicy Bypass -File scripts\collect_local.ps1 -Job fx
#   powershell -ExecutionPolicy Bypass -File scripts\collect_local.ps1 -Job prices
param(
    [Parameter(Mandatory = $true)][ValidateSet("fx", "prices")][string]$Job
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir "local_collect.log"

function Write-Log([string]$msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Job, $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Invoke-Step([string]$name, [string[]]$arguments) {
    Write-Log "start $name"
    # Python logs to stderr; merge it into the log file as plain text.
    $out = & cmd /c "`"$Python`" -m observatory.run $($arguments -join ' ') 2>&1"
    $code = $LASTEXITCODE
    if ($out) { Add-Content -Path $Log -Value $out -Encoding utf8 }
    Write-Log "end $name (exit $code)"
    return $code
}

$env:PYTHONPATH = Join-Path $Root "src"
Write-Log "==== run started on $env:COMPUTERNAME"

# Same order as the workflow: FX on every run, prices only on the daily run.
$null = Invoke-Step "fx" @("fx")
if ($Job -eq "prices") {
    $null = Invoke-Step "prices" @("prices")
}
$null = Invoke-Step "quality" @("quality")

# Commit locally so nothing is lost; pushing stays a manual decision.
& git add data/raw data/quality 2>&1 | Out-Null
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $res = & git commit -m "data: local update $stamp" 2>&1
    Add-Content -Path $Log -Value $res -Encoding utf8
    Write-Log "committed local data ($stamp)"
} else {
    Write-Log "no new data to commit"
}
Write-Log "==== run finished"
