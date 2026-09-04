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
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ConvertTo-Base64Url $bytes
}

function Send-LocalBrowserResponse {
    param(
        [System.IO.Stream]$Stream,
        [string]$Html
    )
    $body = [Text.Encoding]::UTF8.GetBytes($Html)
    $crlf = [string][char]13 + [string][char]10
    $header = "HTTP/1.1 200 OK" + $crlf + "Content-Type: text/html; charset=utf-8" + $crlf + "Content-Length: " + $body.Length + $crlf + "Connection: close" + $crlf + $crlf
    $headerBytes = [Text.Encoding]::ASCII.GetBytes($header)
    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    $Stream.Write($body, 0, $body.Length)
    $Stream.Flush()
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

$listener = $null
$client = $null
$stream = $null
$reader = $null

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

    # TcpListener avoids Windows URL reservation/admin requirements.
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $redirectUri = "http://127.0.0.1:$port/"

    $verifier = New-RandomBase64Url 64
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $challenge = ConvertTo-Base64Url ($sha256.ComputeHash([Text.Encoding]::ASCII.GetBytes($verifier)))
    }
    finally {
        $sha256.Dispose()
    }
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

    $client = $listener.AcceptTcpClient()
    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 1024, $true)

    $requestLine = $reader.ReadLine()
    while ($null -ne ($headerLine = $reader.ReadLine()) -and $headerLine -ne "") {
        # Consume the remaining local HTTP request headers.
    }

    if ([string]::IsNullOrWhiteSpace($requestLine)) {
        throw "沒有收到 Google 授權回傳資料。"
    }

    $parts = $requestLine.Split(" ")
    if ($parts.Length -lt 2) {
        throw "授權回傳格式不正確。"
    }

    $callbackUri = [Uri]("http://127.0.0.1:$port" + $parts[1])
    $callbackQuery = [System.Web.HttpUtility]::ParseQueryString($callbackUri.Query)
    $errorName = $callbackQuery["error"]
    $returnedState = $callbackQuery["state"]
    $code = $callbackQuery["code"]

    if ($errorName) {
        Send-LocalBrowserResponse $stream "<html><meta charset='utf-8'><body><h2>授權未完成</h2><p>你可以關閉此頁後重新執行工具。</p></body></html>"
        throw "Google 授權未完成：$errorName"
    }

    if ($returnedState -ne $state -or [string]::IsNullOrWhiteSpace($code)) {
        Send-LocalBrowserResponse $stream "<html><meta charset='utf-8'><body><h2>授權驗證失敗</h2><p>沒有建立任何 Token。</p></body></html>"
        throw "授權回傳資料驗證失敗，沒有建立任何 Token。"
    }

    Send-LocalBrowserResponse $stream "<html><meta charset='utf-8'><body style='font-family:sans-serif'><h2>Google Drive 授權完成</h2><p>Refresh Token 已複製到筆電剪貼簿。請回到工具視窗。</p><p>現在可以關閉此頁。</p></body></html>"

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
    Show-ErrorAndExit $_.Exception.Message
}
finally {
    if ($null -ne $reader) {
        $reader.Dispose()
    }
    if ($null -ne $stream) {
        $stream.Dispose()
    }
    if ($null -ne $client) {
        $client.Close()
    }
    if ($null -ne $listener) {
        $listener.Stop()
    }
}
