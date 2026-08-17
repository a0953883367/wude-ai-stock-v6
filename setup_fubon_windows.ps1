# 武得 AI 股票助理 - Fubon Neo Windows 自動更新設定
# 請使用一般 PowerShell 執行；若建立排程失敗，再以系統管理員身分執行。

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source

Write-Host "=== 武得 AI 股票助理：富邦 API 自動化設定 ===" -ForegroundColor Cyan
Write-Host "Repository: $Repo"
Write-Host "Python: $Python"

$id = Read-Host "請輸入富邦登入身分證字號"
$cert = Read-Host "請輸入 .pfx 憑證完整路徑"
$apiKey = Read-Host "請輸入 Fubon API Key（若尚未申請可先留白，之後再設定）"

if (-not $id) { throw "FUBON_ID 不可空白" }
if (-not (Test-Path $cert)) { throw "找不到憑證檔：$cert" }

[Environment]::SetEnvironmentVariable("FUBON_ID", $id, "User")
[Environment]::SetEnvironmentVariable("FUBON_CERT_PATH", $cert, "User")
if ($apiKey) {
    [Environment]::SetEnvironmentVariable("FUBON_API_KEY", $apiKey, "User")
    Write-Host "已設定 API Key 登入模式。" -ForegroundColor Green
} else {
    Write-Host "尚未設定 API Key。自動排程前仍需設定 FUBON_PASSWORD 或之後補上 FUBON_API_KEY。" -ForegroundColor Yellow
}

Write-Host "憑證密碼若不是預設值，請自行設定使用者環境變數 FUBON_CERT_PASSWORD。" -ForegroundColor Yellow
Write-Host "基於安全考量，本腳本不把登入密碼或憑證密碼寫入 GitHub。" -ForegroundColor Yellow

function New-WudeTask($Name, $Hour, $Period) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Repo\fubon_runner.py`" --period $Period --auto-git" -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($Hour))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Description "武得 AI 股票助理 Fubon Neo 自動更新" -Force | Out-Null
    Write-Host "已建立：$Name 每日 $($Hour.ToString('00')):00" -ForegroundColor Green
}

New-WudeTask "Wude-Fubon-Morning" 6 "morning"
New-WudeTask "Wude-Fubon-Noon" 12 "noon"
New-WudeTask "Wude-Fubon-Evening" 20 "evening"

Write-Host ""
Write-Host "排程已建立：06:00 / 12:00 / 20:00" -ForegroundColor Green
Write-Host "注意：電腦需開機且可上網；GitHub git push 也需已在本機登入。"
Write-Host "第一次正式啟用前，建議先手動執行：python fubon_runner.py --period noon --no-telegram"
