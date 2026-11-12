$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$supabaseConfigPath = Join-Path $env:APPDATA "WishlistTracker\supabase.json"
$telegramConfigPath = Join-Path $env:APPDATA "WishlistTracker\telegram.json"
if (-not (Test-Path -LiteralPath $supabaseConfigPath)) {
    throw "Сначала настрой Supabase через scripts\setup_wishlist_shortcut.ps1."
}
if (-not (Test-Path -LiteralPath $telegramConfigPath)) {
    throw "Сначала настрой токен и разрешённые ID: scripts\configure_telegram.ps1."
}

$npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
if (-not $npx) {
    $npx = Get-Command npx -ErrorAction SilentlyContinue
}
if (-not $npx) {
    throw "Для Supabase CLI нужен Node.js с npx."
}

$supabaseConfig = Get-Content -LiteralPath $supabaseConfigPath -Raw | ConvertFrom-Json
$telegramConfig = Get-Content -LiteralPath $telegramConfigPath -Raw | ConvertFrom-Json
if (-not $telegramConfig.encryptedToken -or -not $telegramConfig.allowedUserIds) {
    throw "Настройка Telegram неполная. Запусти scripts\configure_telegram.ps1."
}
$projectUrl = ([string]$supabaseConfig.url).TrimEnd("/")
$projectUri = $null
if (-not [Uri]::TryCreate($projectUrl, [UriKind]::Absolute, [ref]$projectUri) -or
    $projectUri.Scheme -ne "https" -or $projectUri.Host -notlike "*.supabase.co") {
    throw "В сохранённой настройке неверный Project URL."
}
$projectRef = $projectUri.Host.Split(".")[0]
$configuredUserIds = @(
    $telegramConfig.allowedUserIds |
        ForEach-Object { [string]$_ } |
        Select-Object -Unique
)
$allowedUserIds = @(
    $configuredUserIds |
        Where-Object {
            $_ -match '^\d{1,16}$' -and
            [decimal]$_ -gt 0 -and
            [decimal]$_ -le 9007199254740991
        }
)
if ($allowedUserIds.Count -eq 0 -or $allowedUserIds.Count -ne $configuredUserIds.Count) {
    throw "Все разрешённые Telegram ID должны быть положительными безопасными целыми числами."
}

$secureToken = ConvertTo-SecureString -String $telegramConfig.encryptedToken
$telegramCredential = New-Object System.Management.Automation.PSCredential("telegram", $secureToken)
$telegramToken = $telegramCredential.GetNetworkCredential().Password
if ($telegramToken -notmatch '^\d+:[A-Za-z0-9_-]+$') {
    throw "Сохранённый токен Telegram повреждён. Настрой бота заново."
}

$timezoneInput = Read-Host "Смещение локального времени от UTC в минутах (Enter = 360, UTC+6)"
$timezoneOffset = 0
if (-not $timezoneInput) {
    $timezoneOffset = 360
} elseif (-not [int]::TryParse($timezoneInput, [ref]$timezoneOffset) -or
    $timezoneOffset -lt -720 -or $timezoneOffset -gt 840) {
    throw "Смещение должно быть целым числом от -720 до 840."
}

function New-RandomSecret {
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $random.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    } finally {
        $random.Dispose()
    }
}

function Write-PrivateFile([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $fileSecurity = New-Object System.Security.AccessControl.FileSecurity
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $fileSecurity.SetOwner($currentUser)
    $fileSecurity.SetAccessRuleProtection($true, $false)
    $fileSecurity.AddAccessRule(
        [System.Security.AccessControl.FileSystemAccessRule]::new(
            $currentUser,
            "FullControl",
            "Allow"
        )
    )
    Set-Acl -LiteralPath $Path -AclObject $fileSecurity
}

$webhookSecret = New-RandomSecret
$cronSecret = New-RandomSecret
$internalSecret = New-RandomSecret
$functionUrl = "$projectUrl/functions/v1/telegram-bot"
$secretsPath = Join-Path $env:TEMP ("wishlist-telegram-secrets-" + [Guid]::NewGuid().ToString("N") + ".env")
$sqlPath = Join-Path $env:TEMP ("wishlist-telegram-setup-" + [Guid]::NewGuid().ToString("N") + ".sql")

try {
    $secrets = @(
        "TELEGRAM_BOT_TOKEN=$telegramToken"
        "TELEGRAM_ALLOWED_USER_IDS=$($allowedUserIds -join ',')"
        "TELEGRAM_WEBHOOK_SECRET=$webhookSecret"
        "TELEGRAM_CRON_SECRET=$cronSecret"
        "TELEGRAM_INTERNAL_SECRET=$internalSecret"
        "BOT_TIMEZONE_OFFSET_MINUTES=$timezoneOffset"
    ) -join "`r`n"
    Write-PrivateFile $secretsPath $secrets

    $migrationPath = Join-Path $projectRoot "supabase\telegram_cloud.sql"
    $migrationSql = Get-Content -LiteralPath $migrationPath -Raw
    $databaseSql = @"
create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;

do `$vault_setup`$
declare
  secret_id uuid;
begin
  select id into secret_id from vault.secrets where name = 'wishlist_telegram_function_url';
  if secret_id is null then
    perform vault.create_secret('$functionUrl', 'wishlist_telegram_function_url', 'Telegram Edge Function URL');
  else
    perform vault.update_secret(secret_id, '$functionUrl', 'wishlist_telegram_function_url', 'Telegram Edge Function URL');
  end if;

  select id into secret_id from vault.secrets where name = 'wishlist_telegram_cron_secret';
  if secret_id is null then
    perform vault.create_secret('$cronSecret', 'wishlist_telegram_cron_secret', 'Telegram reminder dispatcher secret');
  else
    perform vault.update_secret(secret_id, '$cronSecret', 'wishlist_telegram_cron_secret', 'Telegram reminder dispatcher secret');
  end if;

  select id into secret_id from vault.secrets where name = 'wishlist_telegram_internal_secret';
  if secret_id is null then
    perform vault.create_secret('$internalSecret', 'wishlist_telegram_internal_secret', 'Telegram completion notification secret');
  else
    perform vault.update_secret(secret_id, '$internalSecret', 'wishlist_telegram_internal_secret', 'Telegram completion notification secret');
  end if;
end
`$vault_setup`$;

$migrationSql

do `$unschedule_old`$
declare
  old_job_id bigint;
begin
  for old_job_id in select jobid from cron.job where jobname = 'wishlist-telegram-reminders'
  loop
    perform cron.unschedule(old_job_id);
  end loop;
end
`$unschedule_old`$;

select cron.schedule(
  'wishlist-telegram-reminders',
  '* * * * *',
  `$dispatch_job`$
    select net.http_post(
      url := (
        select decrypted_secret from vault.decrypted_secrets
        where name = 'wishlist_telegram_function_url' limit 1
      ),
      headers := jsonb_build_object(
        'content-type', 'application/json',
        'x-telegram-cron-secret', (
          select decrypted_secret from vault.decrypted_secrets
          where name = 'wishlist_telegram_cron_secret' limit 1
        )
      ),
      body := '{"action":"dispatch_reminders"}'::jsonb
    );
  `$dispatch_job`$
);
"@
    Write-PrivateFile $sqlPath $databaseSql

    Push-Location $projectRoot
    try {
        Write-Host "Supabase CLI попросит подтвердить SQL-настройку production до остальных изменений."
        & $npx.Source --yes supabase db query "--remote=$projectRef" --file $sqlPath
        if ($LASTEXITCODE -ne 0) {
            throw "SQL миграция Telegram не применена. Production-данные не удаляются; проверь вывод CLI."
        }

        & $npx.Source --yes supabase secrets set "--remote=$projectRef" --env-file $secretsPath
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось сохранить Telegram secrets в Supabase."
        }

        & $npx.Source --yes supabase functions deploy telegram-bot "--remote=$projectRef"
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось опубликовать Edge Function telegram-bot."
        }
    } finally {
        Pop-Location
    }

    try {
        $webhookResult = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$telegramToken/setWebhook" `
            -Method Post `
            -ContentType "application/json" `
            -Body (@{
                url = $functionUrl
                secret_token = $webhookSecret
                allowed_updates = @("message")
            } | ConvertTo-Json -Compress)
        if (-not $webhookResult.ok) {
            throw "Telegram rejected the webhook."
        }
        $webhookInfo = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$telegramToken/getWebhookInfo" `
            -Method Get
        if ($webhookInfo.result.url -ne $functionUrl) {
            throw "Telegram webhook verification failed."
        }
    } catch {
        throw "Function deployed, but Telegram webhook could not be verified. Re-run this script after checking network access."
    }

    $telegramConfig | Add-Member -NotePropertyName cloudManaged -NotePropertyValue $true -Force
    [System.IO.File]::WriteAllText(
        $telegramConfigPath,
        ($telegramConfig | ConvertTo-Json),
        (New-Object System.Text.UTF8Encoding($false))
    )
} finally {
    Remove-Item -LiteralPath $secretsPath, $sqlPath -Force -ErrorAction SilentlyContinue
    Remove-Variable `
        telegramToken, secureToken, telegramCredential, allowedUserIds, configuredUserIds, `
        webhookSecret, cronSecret, internalSecret, secrets, databaseSql, migrationSql `
        -ErrorAction SilentlyContinue
}

Write-Host "Telegram bot deployed to Supabase. Bot token and webhook secrets are stored as Supabase Function Secrets."
Write-Host "Restart the website with its desktop shortcut. The local Telegram polling will then be disabled."
Write-Host "Each allowed user must open the bot and press Start once."
