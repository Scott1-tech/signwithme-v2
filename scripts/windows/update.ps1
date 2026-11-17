# Get the newest version of SignWithMe from GitHub and restart it with it.
#   powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1
# Data (database, files) stays: it lives in Docker volumes, not in the code.

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

Write-Host '== Backup first' -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'backup.ps1')

Write-Host '== Newest code' -ForegroundColor Cyan
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw 'git pull failed (local changes?). Nothing was restarted.' }

$tunnelToken = (Get-Content '.env' | Where-Object { $_ -like 'CLOUDFLARE_TUNNEL_TOKEN=*' } | Select-Object -First 1)
$profileArgs = @()
if ($tunnelToken -and ($tunnelToken.Substring('CLOUDFLARE_TUNNEL_TOKEN='.Length).Trim().Length -gt 0)) { $profileArgs = @('--profile', 'tunnel') }

Write-Host '== Rebuild and restart' -ForegroundColor Cyan
docker compose @profileArgs up --build -d
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed; see the messages above.' }
Write-Host 'Updated.' -ForegroundColor Green
