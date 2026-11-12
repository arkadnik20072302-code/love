$ErrorActionPreference = "Stop"

$configDirectory = Join-Path $env:APPDATA "WishlistTracker"
$configPath = Join-Path $configDirectory "telegram.json"
if (Test-Path -LiteralPath $configPath) {
    $previousConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if (-not $previousConfig.encryptedToken) {
        throw "В сохранённой настройке нет токена. Настрой бота заново."
    }
    $tokenSecure = ConvertTo-SecureString -String $previousConfig.encryptedToken
    $allowedUserIds = @($previousConfig.allowedUserIds | ForEach-Object { [string]$_ })
    $idsPrompt = "Добавь Telegram ID (например ID девушки), через запятую"
} else {
    $tokenSecure = Read-Host "Вставь токен бота от @BotFather" -AsSecureString
    $token = (New-Object System.Management.Automation.PSCredential("telegram", $tokenSecure)).GetNetworkCredential().Password
    if ($token -notmatch '^\d+:[A-Za-z0-9_-]+$') {
        throw "Токен выглядит некорректно. Скопируй его заново у @BotFather."
    }
    $allowedUserIds = @()
    $idsPrompt = "Введи свой числовой Telegram ID"
}

$idsInput = Read-Host "$idsPrompt (узнать можно у @userinfobot)"
$newUserIds = @(
    $idsInput -split "," |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
if ($newUserIds.Count -eq 0 -or @($newUserIds | Where-Object { $_ -notmatch '^\d+$' }).Count -gt 0) {
    throw "Нужно указать один или несколько числовых Telegram ID."
}
$allowedUserIds = @($allowedUserIds + $newUserIds | Select-Object -Unique)
$cloudManaged = [bool]$previousConfig.cloudManaged

New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
$config = @{
    encryptedToken = ConvertFrom-SecureString -SecureString $tokenSecure
    allowedUserIds = @($allowedUserIds | Select-Object -Unique)
    cloudManaged = $cloudManaged
} | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $configPath,
    $config,
    (New-Object System.Text.UTF8Encoding($false))
)

Remove-Variable token, tokenSecure, previousConfig -ErrorAction SilentlyContinue
Write-Host "Telegram настроен. Токен зашифрован средствами Windows для текущего пользователя."
Write-Host "Разрешённые ID: $($allowedUserIds -join ', '). Каждый пользователь должен открыть бота и нажать Start."
if ($cloudManaged) {
    Write-Host "Облачный бот уже включён: повторно запусти scripts\deploy_telegram_bot.ps1, чтобы применить новый список ID."
}
Write-Host "Перезапусти сайт ярлыком «Наша карта желаний», чтобы применить изменения."
