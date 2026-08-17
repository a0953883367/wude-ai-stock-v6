param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "noon", "evening")]
    [string]$Period
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $env:LOCALAPPDATA "WudeAI\logs"
$LogFile = Join-Path $LogDir "fubon-$Period.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $Repo

try {
    & python "$Repo\fubon_runner.py" --period $Period --auto-git *>> $LogFile
    exit $LASTEXITCODE
}
catch {
    "$(Get-Date -Format s) ERROR: $($_.Exception.Message)" | Add-Content -Path $LogFile
    exit 1
}
