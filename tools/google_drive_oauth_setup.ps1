#requires -Version 5.1
<#
.SYNOPSIS
  Wude AI Stock Archive - Google Drive OAuth one-time authorization helper.

.DESCRIPTION
  Selects the Desktop OAuth client JSON downloaded from Google Cloud, opens the
  official Google authorization page, requests only drive.file, and copies the
  resulting refresh token to the Windows clipboard.

.SECURITY
  The client JSON and refresh token are never uploaded, written to this repo, or
  printed to the console. Keep both private.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function ConvertTo-Base64Url {
    param([byte[]]$Bytes)
    return [Convert]::ToBase64String($Bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function New-RandomBase64Url {
    param([int]$Length = 64)
    $bytes = New-Object byte[] $Length
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ConvertTo-Base64Url $bytes
}

function Show-ErrorAndExit {
    param([string]$Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "Wude AI Google Drive 授權",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Web

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "選擇從 Google Cloud 下載的 OAuth 用戶端 JSON"
$dialog.Filter = "Google OAuth JSON (*.json)|*.json"
$dialog.InitialDirectory = [Environment]::GetFolderPath("UserProfile") + "\Downloads"
$dialog.Multiselect = $false

if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 0
}

try {
    $json = Get-Content -LiteralPath $dialog.FileName -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $json.installed) {
        Show-ErrorAndExit "這不是「電腦版應用程式」OAuth JSON。請回 Google Cloud 下載正確的用戶端 JSON。"
    }

    $clientId = [string]$json.installed.client_id
    $clientSecret = [string]$json.installed.client_secret
    $authUri = [string]$json.installed.auth_uri
    $tokenUri = [string]$json.installed.token_uri

    if ([string]::IsNullOrWhiteSpace($clientId) -or
        [string]::IsNullOrWhiteSpace($clientSecret) -or
        [string]::IsNullOrWhiteSpace($authUri) -or
        [string]::IsNullOrWhiteSpace($tokenUri)) {
        Show-ErrorAndExit "OAuth JSON 缺少必要欄位，請重新從 Google Cloud 下載。"
    }

    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $port = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
    $probe.Stop()

    $redirectUri = "http://127.0.0.1:$port/"
    $listener = New-Object System.Net.HttpListener
    $listener.Prefixes.Add($redirectUri)
    $listener.Start()

    $verifier = New-RandomBase64Url 64
    $sha = [Security.Cryptography.SHA256]::Create()
    $challenge = ConvertTo-Base64Url ($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($verifier)))
    $state = New-RandomBase64Url 32
    $scope = "https://www.googleapis.com/auth/drive.file"

    $query = [System.Web.HttpUtility]::ParseQueryString("")
    $query["client_id"] = $clientId
    $query["redirect_uri"] = $redirectUri
    $query["response_type"] = "code"
    $query["scope"] = $scope
    $query["access_type"] = "offline"
    $query["prompt"] = "consent"
    $query["state"] = $state
    $query["code_challenge"] = $challenge
    $query["code_challenge_method"] = "S256"

    $authorizeUrl = $authUri + "?" + $query.ToString()
    Start-Process $authorizeUrl

    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response

    $errorName = $request.QueryString["error"]
    $returnedState = $request.QueryString["state"]
    $code = $request.QueryString["code"]

    if ($errorName) {
        $html = "<html><meta charset='utf-8'><body><h2>授權未完成</h2><p>你可以關閉此頁後重新執行工具。</p></body></html>"
        $buffer = [Text.Encoding]::UTF8.GetBytes($html)
        $response.ContentType = "text/html; charset=utf-8"
        $response.OutputStream.Write($buffer, 0, $buffer.Length)
        $response.Close()
        throw "Google 授權未完成：$errorName"
    }

    if ($returnedState -ne $state -or [string]::IsNullOrWhiteSpace($code)) {
        throw "授權回傳資料驗證失敗，沒有建立任何 Token。"
    }

    $html = "<html><meta charset='utf-8'><body style='font-family:sans-serif'><h2>Google Drive 授權完成</h2><p>Refresh Token 已複製到筆電剪貼簿。請回到工具視窗。</p><p>現在可以關閉此頁。</p></body></html>"
    $buffer = [Text.Encoding]::UTF8.GetBytes($html)
    $response.ContentType = "text/html; charset=utf-8"
    $response.OutputStream.Write($buffer, 0, $buffer.Length)
    $response.Close()
    $listener.Stop()

    $tokenBody = @{
        client_id = $clientId
        client_secret = $clientSecret
        code = $code
        code_verifier = $verifier
        grant_type = "authorization_code"
        redirect_uri = $redirectUri
    }

    $token = Invoke-RestMethod -Uri $tokenUri -Method Post -ContentType "application/x-www-form-urlencoded" -Body $tokenBody
    $refreshToken = [string]$token.refresh_token

    if ([string]::IsNullOrWhiteSpace($refreshToken)) {
        throw "Google 沒有回傳 Refresh Token。請撤銷舊授權後再執行一次。"
    }

    Set-Clipboard -Value $refreshToken
    $lineBreak = [Environment]::NewLine

    [System.Windows.Forms.MessageBox]::Show(
        "授權成功。Refresh Token 已複製到剪貼簿，沒有存成檔案，也沒有顯示在畫面上。" + $lineBreak + $lineBreak + "下一步：到 GitHub 建立 GOOGLE_DRIVE_REFRESH_TOKEN，直接貼上後儲存。",
        "Wude AI Google Drive 授權完成",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null

    $refreshToken = $null
    $token = $null
}
catch {
    if ($listener -and $listener.IsListening) {
        $listener.Stop()
    }
    Show-ErrorAndExit $_.Exception.Message
}
