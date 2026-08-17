# 武得 AI 股票助理：富邦行情一鍵自動化
# 金鑰與憑證密碼只從 Windows 認證管理員讀取，不寫入本檔或 GitHub。

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Runner = Join-Path $Repo "run_fubon_windows.ps1"
$Cert = Join-Path $env:LOCALAPPDATA "WudeAI\cert\fubon_cert.p12"

Write-Host "=== 武得 AI 股票助理：富邦行情一鍵設定 ===" -ForegroundColor Cyan
if (-not (Test-Path $Cert)) { throw "找不到固定憑證：$Cert" }

& $Python -m pip install --user "keyring>=25,<26"
if ($LASTEXITCODE -ne 0) { throw "keyring 安裝失敗" }

& $Python -c "import fubon_neo"
if ($LASTEXITCODE -ne 0) { throw "找不到富邦 SDK，請先安裝官方 Windows 64 位元 SDK" }

Set-Location $Repo
& $Python "$Repo\fubon_runner.py" --check
if ($LASTEXITCODE -ne 0) { throw "富邦登入或行情驗證失敗" }

function Install-WudeTask([string]$Name, [string]$At, [string]$Period) {
    $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Period $Period"
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $Repo
    $Trigger = New-ScheduledTaskTrigger -Daily -At $At
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings -Description "武得 AI 股票助理：富邦唯讀行情更新" -Force | Out-Null
    Write-Host "已建立 $Name（$At）" -ForegroundColor Green
}

Install-WudeTask "Wude-Fubon-Morning" "06:00" "morning"
Install-WudeTask "Wude-Fubon-Noon" "12:00" "noon"
Install-WudeTask "Wude-Fubon-Evening" "20:00" "evening"

Write-Host "設定完成：富邦登入、行情與三個自動排程均已啟用。" -ForegroundColor Green
Write-Host "此程式只使用行情權限，不會自動下單。"
Write-Host "電腦需在執行時間開機並連上網路；錯過時間會在下次開機後補跑。"
