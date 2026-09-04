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

function Show-ErrorMessage {
    param([string]$Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "Wude AI Google Drive Authorization",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Web

$listener = $null
$client = $null
$stream = $null
$reader = $null
$browserResponseSent = $false
$failed = $false

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "Select the Desktop OAuth client JSON downloaded from Google Cloud"
$dialog.Filter = "Google OAuth JSON (*.json)|*.json"
$dialog.InitialDirectory = [Environment]::GetFolderPath("UserProfile") + "\Downloads"
$dialog.Multiselect = $false

if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 0
}

try {
    $json = Get-Content -LiteralPath $dialog.FileName -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $json.installed) {
        throw "This is not a Desktop app OAuth JSON. Download the correct client JSON from Google Cloud."
    }

    $clientId = [string]$json.installed.client_id
    $clientSecret = [string]$json.installed.client_secret
    $authUri = [string]$json.installed.auth_uri
    $tokenUri = [string]$json.installed.token_uri

    if ([string]::IsNullOrWhiteSpace($clientId) -or
        [string]::IsNullOrWhiteSpace($clientSecret) -or
        [string]::IsNullOrWhiteSpace($authUri) -or
        [string]::IsNullOrWhiteSpace($tokenUri)) {
        throw "The OAuth JSON is missing required fields. Download it again from Google Cloud."
    }

    # TcpListener avoids Windows URL reservation and administrator requirements.
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
        throw "No authorization response was received from Google."
    }

    $parts = $requestLine.Split(" ")
    if ($parts.Length -lt 2) {
        throw "The authorization response format is invalid."
    }

    $callbackUri = [Uri]("http://127.0.0.1:$port" + $parts[1])
    $callbackQuery = [System.Web.HttpUtility]::ParseQueryString($callbackUri.Query)
    $errorName = $callbackQuery["error"]
    $returnedState = $callbackQuery["state"]
    $code = $callbackQuery["code"]

    if ($errorName) {
        throw "Google authorization was not completed: $errorName"
    }

    if ($returnedState -ne $state -or [string]::IsNullOrWhiteSpace($code)) {
        throw "Authorization response validation failed. No token was created."
    }

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
        throw "Google did not return a refresh token. Revoke the old authorization and run this tool again."
    }

    Set-Clipboard -Value $refreshToken

    Send-LocalBrowserResponse $stream "<html><meta charset='utf-8'><body style='font-family:sans-serif'><h2>Google Drive authorization completed</h2><p>The refresh token was copied to the Windows clipboard.</p><p>You may close this page and return to the tool.</p></body></html>"
    $browserResponseSent = $true

    [System.Windows.Forms.MessageBox]::Show(
        "Authorization succeeded. The refresh token was copied to the clipboard. It was not saved to a file or printed. Next, create the GitHub secret GOOGLE_DRIVE_REFRESH_TOKEN and paste it there.",
        "Wude AI Google Drive Authorization Complete",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null

    $refreshToken = $null
    $token = $null
}
catch {
    $failed = $true
    if ($null -ne $stream -and -not $browserResponseSent) {
        try {
            Send-LocalBrowserResponse $stream "<html><meta charset='utf-8'><body style='font-family:sans-serif'><h2>Authorization was not completed</h2><p>No token was saved. Close this page and run the tool again.</p></body></html>"
            $browserResponseSent = $true
        }
        catch {
            # The local browser connection may already be closed.
        }
    }
    Show-ErrorMessage $_.Exception.Message
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

if ($failed) {
    exit 1
}
