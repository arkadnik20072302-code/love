$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$supabaseConfigPath = Join-Path $env:APPDATA "WishlistTracker\supabase.json"
if (-not (Test-Path -LiteralPath $supabaseConfigPath)) {
    throw "Configure Supabase first using scripts\setup_wishlist_shortcut.ps1."
}

$npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
if (-not $npx) {
    $npx = Get-Command npx -ErrorAction SilentlyContinue
}
if (-not $npx) {
    throw "Node.js with npx is required for the Supabase CLI."
}

$supabaseConfig = Get-Content -LiteralPath $supabaseConfigPath -Raw | ConvertFrom-Json
$projectUrl = [string]$supabaseConfig.url
$projectUri = $null
if (-not [Uri]::TryCreate($projectUrl, [UriKind]::Absolute, [ref]$projectUri) -or
    $projectUri.Scheme -ne "https" -or $projectUri.Host -notlike "*.supabase.co") {
    throw "The saved Supabase Project URL is invalid."
}
$projectRef = $projectUri.Host.Split(".")[0]
$secureSupabaseKey = ConvertTo-SecureString -String $supabaseConfig.encryptedKey
$supabaseCredential = New-Object System.Management.Automation.PSCredential("supabase", $secureSupabaseKey)
$supabaseKey = $supabaseCredential.GetNetworkCredential().Password

$secureGeminiKey = Read-Host "Enter the Gemini API key (Google AI Studio)" -AsSecureString
$geminiKey = (New-Object System.Management.Automation.PSCredential("gemini", $secureGeminiKey)).GetNetworkCredential().Password
if (-not $geminiKey -or $geminiKey.Contains("`r") -or $geminiKey.Contains("`n")) {
    throw "A valid Gemini API key is required."
}

$hmac = [System.Security.Cryptography.HMACSHA256]::new(
    [System.Text.Encoding]::UTF8.GetBytes($supabaseKey)
)
try {
    $voiceSecretBytes = $hmac.ComputeHash(
        [System.Text.Encoding]::UTF8.GetBytes("wishlist-voice-function-v1")
    )
} finally {
    $hmac.Dispose()
}
$voiceSecret = ([BitConverter]::ToString($voiceSecretBytes)).Replace("-", "").ToLowerInvariant()
$secretsPath = Join-Path $env:TEMP ("wishlist-edge-secrets-" + [Guid]::NewGuid().ToString("N") + ".env")

try {
    [System.IO.File]::WriteAllText(
        $secretsPath,
        "GEMINI_API_KEY=$geminiKey`r`nVOICE_FUNCTION_SECRET=$voiceSecret`r`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    $fileSecurity = New-Object System.Security.AccessControl.FileSecurity
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $fileSecurity.SetOwner($currentUser)
    $fileSecurity.SetAccessRuleProtection($true, $false)
    $fileSecurity.AddAccessRule(
        [System.Security.AccessControl.FileSystemAccessRule]::new($currentUser, "FullControl", "Allow")
    )
    Set-Acl -LiteralPath $secretsPath -AclObject $fileSecurity

    Push-Location $projectRoot
    try {
        & $npx.Source --yes supabase secrets set --project-ref $projectRef --env-file $secretsPath
        if ($LASTEXITCODE -ne 0) {
            throw "Supabase could not save the secrets. Check access and try again."
        }
        & $npx.Source --yes supabase functions deploy voice-reminder "--remote=$projectRef"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to deploy the Edge Function."
        }
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item -LiteralPath $secretsPath -Force -ErrorAction SilentlyContinue
    Remove-Variable geminiKey, supabaseKey, voiceSecret -ErrorAction SilentlyContinue
}

Write-Host "Edge Function voice-reminder deployed. Restart the site using its desktop shortcut."
