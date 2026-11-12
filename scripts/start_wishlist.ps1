$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $env:APPDATA "WishlistTracker\supabase.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Сначала один раз запусти scripts\setup_wishlist_shortcut.ps1 из папки проекта."
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$secureKey = ConvertTo-SecureString -String $config.encryptedKey
$credential = New-Object System.Management.Automation.PSCredential("supabase", $secureKey)
$env:SUPABASE_URL = [string]$config.url
$env:SUPABASE_SECRET_KEY = $credential.GetNetworkCredential().Password
$env:PORT = "8765"

$telegramConfigPath = Join-Path $env:APPDATA "WishlistTracker\telegram.json"
$cloudTelegramManaged = $false
if (Test-Path -LiteralPath $telegramConfigPath) {
    $telegramConfig = Get-Content -LiteralPath $telegramConfigPath -Raw | ConvertFrom-Json
    if (-not $telegramConfig.encryptedToken -or -not $telegramConfig.allowedUserIds) {
        throw "Настройка Telegram повреждена. Запусти scripts\configure_telegram.ps1 ещё раз."
    }
    $cloudTelegramManaged = [bool]$telegramConfig.cloudManaged
    if ($cloudTelegramManaged) {
        $env:TELEGRAM_MODE = "cloud"
    } else {
        $telegramSecureToken = ConvertTo-SecureString -String $telegramConfig.encryptedToken
        $telegramCredential = New-Object System.Management.Automation.PSCredential("telegram", $telegramSecureToken)
        $env:TELEGRAM_BOT_TOKEN = $telegramCredential.GetNetworkCredential().Password
        $env:TELEGRAM_ALLOWED_USER_IDS = (@($telegramConfig.allowedUserIds) -join ",")
    }
}

$siteUrl = "http://127.0.0.1:8765"
$existingServer = $null
try {
    $existingServer = Invoke-RestMethod "$siteUrl/health" -TimeoutSec 2
} catch {
    $existingServer = $null
}

if ($existingServer -and $existingServer.ok) {
    if ($existingServer.storage -ne "supabase") {
        Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue
        throw "На порту 8765 уже запущен старый сервер ($($existingServer.storage)). Останови его через Ctrl+C и нажми ярлык ещё раз."
    }
    if ($cloudTelegramManaged -and
        (-not $existingServer.telegram -or -not $existingServer.telegram.cloudManaged)) {
        Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue
        throw "Сайт запущен без облачного режима Telegram. Закрой старое окно сервера (`Ctrl+C`) и снова нажми ярлык."
    }
    if ((Test-Path -LiteralPath $telegramConfigPath) -and
        (-not $cloudTelegramManaged) -and
        (-not $existingServer.telegram -or
            -not $existingServer.telegram.enabled -or
            -not $existingServer.telegram.running)) {
        Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue
        throw "Сайт уже запущен, но без Telegram. Закрой окно старого сервера сочетанием Ctrl+C и снова запусти ярлык."
    }
    if ($existingServer.telegram -and $existingServer.telegram.error) {
        $telegramError = [string]$existingServer.telegram.error
        Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue
        throw "Telegram не работает: $telegramError"
    }
    Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue
    Start-Process $siteUrl
    $telegramMessage = if ($cloudTelegramManaged) { "Telegram работает в Supabase" } else { "Telegram работает" }
    Write-Host "Сайт и Telegram уже запущены ($telegramMessage). Для диагностики открой $siteUrl/health."
    exit 0
}

$python = (Get-Command python.exe -ErrorAction Stop).Source
$process = Start-Process -FilePath $python -ArgumentList "app.py" -WorkingDirectory $projectRoot -PassThru
Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY, Env:\PORT, Env:\TELEGRAM_BOT_TOKEN, Env:\TELEGRAM_ALLOWED_USER_IDS, Env:\TELEGRAM_MODE -ErrorAction SilentlyContinue

$health = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod "$siteUrl/health" -TimeoutSec 2
        if ($health.ok) {
            break
        }
    } catch {
        if ($process.HasExited) {
            throw "Сервер завершился с кодом $($process.ExitCode). Проверь окно Python для сообщения об ошибке."
        }
    }
}

if (-not $health -or -not $health.ok) {
    throw "Сервер не ответил на порту 8765. Проверь окно Python для сообщения об ошибке."
}
if ($health.storage -ne "supabase") {
    throw "Сайт запустился, но сообщил о хранилище '$($health.storage)' вместо Supabase."
}
if (Test-Path -LiteralPath $telegramConfigPath) {
    $telegramReady = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        if ($cloudTelegramManaged -and $health.telegram -and $health.telegram.cloudManaged) {
            $telegramReady = $true
            break
        }
        if (-not $cloudTelegramManaged -and $health.telegram -and $health.telegram.running) {
            $telegramReady = $true
            break
        }
        Start-Sleep -Milliseconds 300
        $health = Invoke-RestMethod "$siteUrl/health" -TimeoutSec 2
    }
    if (-not $telegramReady) {
        $telegramError = if ($health.telegram.error) { $health.telegram.error } elseif ($cloudTelegramManaged) { "облачный режим не включился." } else { "бот не запустил опрос Telegram." }
        throw "Сайт запущен, но Telegram не работает: $telegramError Открой $siteUrl/health для диагностики."
    }
}

Start-Process $siteUrl
$telegramMessage = if ($cloudTelegramManaged) { "Бот управляется Supabase" } else { "Telegram активен локально" }
Write-Host "Сайт запущен и подключён к Supabase. $telegramMessage. Диагностика: $siteUrl/health"
