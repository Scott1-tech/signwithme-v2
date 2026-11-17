# SignWithMe on an office Windows PC: first start.
#
#   Right-click PowerShell -> "Run as administrator", then:
#     cd C:\signwithme-v2
#     powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
#
# What it does:
#   1. checks Docker Desktop is running;
#   2. creates .env (only if there is none) with random secrets and your admin
#      account; asks for a Cloudflare Tunnel token (leave empty for office only);
#   3. opens port 3000 in Windows Firewall for the office network (Private);
#   4. builds and starts the app, and prints the addresses to open.
# Running it again is safe: an existing .env is kept as it is.

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

function New-Secret([int]$bytes = 36) {
    $buffer = New-Object byte[] $bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buffer)
    $rng.Dispose()
    # URL-safe: no characters that need quoting in .env.
    return ([Convert]::ToBase64String($buffer)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-OfficeIp {
    $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'vEthernet|WSL|Docker|Loopback' }
    $first = $candidates | Select-Object -First 1
    if ($first) { return $first.IPAddress }
    return 'localhost'
}

Write-Step 'Docker'
try {
    docker version --format '{{.Server.Version}}' | Out-Null
} catch {
    Write-Host 'Docker Desktop is not running. Start Docker Desktop, wait until it says "Engine running", then run this again.' -ForegroundColor Red
    exit 1
}
docker compose version | Out-Null
Write-Host 'Docker is running.'

$officeIp = Get-OfficeIp

Write-Step '.env'
if (Test-Path '.env') {
    Write-Host '.env already exists: kept as it is.'
} else {
    $email = Read-Host 'Admin e-mail (you sign in with it)'
    if (-not $email) { throw 'An admin e-mail is required.' }
    $secure = Read-Host 'Admin password (at least 10 characters)' -AsSecureString
    $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($password.Length -lt 10) { throw 'The password is too short.' }
    $token = Read-Host 'Cloudflare Tunnel token (Enter = office network only)'

    $lines = @(
        '# Created by scripts\windows\setup.ps1. Keep a copy of this file somewhere safe:',
        '# without PII_ENCRYPTION_KEY the stored SSN / EIN values cannot be read back.',
        "DB_PASSWORD=$(New-Secret 24)",
        'STORAGE_BACKEND=local',
        "JWT_SECRET=$(New-Secret)",
        "PII_ENCRYPTION_KEY=$(New-Secret)",
        '# MinIO is not used (files are kept on this PC); compose still wants the names.',
        'MINIO_USER=minio-not-used',
        "MINIO_PASSWORD=$(New-Secret 18)",
        'MINIO_BUCKET=contracts',
        '# 0.0.0.0 = other computers in the office can open the app.',
        'FRONTEND_BIND=0.0.0.0',
        "SHARE_BASE_URL=http://$($officeIp):3000",
        "CORS_ORIGINS=http://localhost:3000,http://$($officeIp):3000",
        'DEBUG=false',
        '# Only an admin creates accounts.',
        'ALLOW_SELF_REGISTRATION=false',
        "SEED_ADMIN_EMAIL=$email",
        "SEED_ADMIN_PASSWORD=$password",
        'SEED_ADMIN_NAME=Administrator',
        "CLOUDFLARE_TUNNEL_TOKEN=$token"
    )
    # UTF-8 WITHOUT a byte-order mark: Docker reads a BOM as part of the first name.
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '.env created.'
}

$tunnelToken = (Get-Content '.env' | Where-Object { $_ -like 'CLOUDFLARE_TUNNEL_TOKEN=*' } | Select-Object -First 1)
$useTunnel = $tunnelToken -and ($tunnelToken.Substring('CLOUDFLARE_TUNNEL_TOKEN='.Length).Trim().Length -gt 0)

Write-Step 'Windows Firewall (port 3000, office network only)'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    if (-not (Get-NetFirewallRule -DisplayName 'SignWithMe 3000' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName 'SignWithMe 3000' -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private,Domain | Out-Null
        Write-Host 'Rule added.'
    } else {
        Write-Host 'Rule already there.'
    }
} else {
    Write-Host 'Not run as administrator: the firewall rule was not added. Other office computers may not reach the app.' -ForegroundColor Yellow
}

Write-Step 'Building and starting (the first time takes 5-15 minutes)'
$profileArgs = @()
if ($useTunnel) { $profileArgs = @('--profile', 'tunnel') }
docker compose @profileArgs up --build -d
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed; see the messages above.' }

Write-Step 'Waiting for the app'
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $answer = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:3000/api/fonts?category=signature' -TimeoutSec 5
        if ($answer.StatusCode -lt 500) { $ready = $true; break }
    } catch {
        # 401 means the API answered: good enough.
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -lt 500) { $ready = $true; break }
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    Write-Host 'The app did not answer yet. See: docker compose logs backend' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'SignWithMe is running.' -ForegroundColor Green
Write-Host "  On this PC:            http://localhost:3000"
Write-Host "  From the office:       http://$($officeIp):3000"
if ($useTunnel) { Write-Host '  From the internet:     the public hostname you set for the tunnel in Cloudflare' }
Write-Host ''
Write-Host 'Back up regularly: scripts\windows\backup.ps1'
