$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$credentialsPath = Join-Path $PSScriptRoot "wishlist_credentials.ps1"
$templatePath = Join-Path $PSScriptRoot "wishlist_credentials.example.ps1"

if (-not (Test-Path -LiteralPath $credentialsPath)) {
    Copy-Item -LiteralPath $templatePath -Destination $credentialsPath
    Write-Host "Создал scripts\wishlist_credentials.ps1."
    Write-Host "Открой этот файл, замени ProjectUrl и SecretKey, сохрани и запусти установщик ещё раз."
    exit 0
}

$settings = $null
try {
    $settings = & $credentialsPath
    if ($settings -isnot [System.Collections.IDictionary]) {
        throw "Файл wishlist_credentials.ps1 должен возвращать hashtable с ProjectUrl и SecretKey."
    }
    $projectUrl = ([string]$settings.ProjectUrl).Trim()
    $key = ([string]$settings.SecretKey).Trim()
} finally {
    Copy-Item -LiteralPath $templatePath -Destination $credentialsPath -Force
}

$parsedUrl = $null
if (-not [Uri]::TryCreate($projectUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or
    $parsedUrl.Scheme -ne "https" -or
    $parsedUrl.Host -notlike "*.supabase.co") {
    throw "В wishlist_credentials.ps1 нужен ProjectUrl вида https://xxxxx.supabase.co."
}
if (-not ($key.StartsWith("sb_secret_") -or $key.Split(".").Count -eq 3)) {
    throw "В wishlist_credentials.ps1 вставь полный Secret key или legacy service_role JWT, не Publishable/anon key."
}

$secureKey = ConvertTo-SecureString -String $key -AsPlainText -Force
$python = (Get-Command python.exe -ErrorAction Stop).Source
$env:SUPABASE_URL = $parsedUrl.AbsoluteUri.TrimEnd("/")
$env:SUPABASE_SECRET_KEY = $key
try {
    & $python (Join-Path $PSScriptRoot "check_supabase.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Проверка Supabase завершилась с ошибкой. Ярлык пока не создан."
    }
} finally {
    Remove-Item Env:\SUPABASE_URL, Env:\SUPABASE_SECRET_KEY -ErrorAction SilentlyContinue
    Remove-Variable key, settings -ErrorAction SilentlyContinue
}

$configDirectory = Join-Path $env:APPDATA "WishlistTracker"
$configPath = Join-Path $configDirectory "supabase.json"
New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
$config = @{
    url = $parsedUrl.AbsoluteUri.TrimEnd("/")
    encryptedKey = ConvertFrom-SecureString -SecureString $secureKey
} | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $configPath,
    $config,
    (New-Object System.Text.UTF8Encoding($false))
)

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Наша карта желаний.lnk"
$startScript = Join-Path $PSScriptRoot "start_wishlist.ps1"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "Запустить сайт карты желаний"
$shortcut.Save()

Write-Host "Настройки сохранены в зашифрованном виде для текущего пользователя Windows."
Write-Host "Временный файл с ключом очищен."
Write-Host "Создан ярлык: $shortcutPath"
Write-Host "Запускаю сайт..."
& $startScript
