# Back up SignWithMe: the database and the stored files, together.
#   powershell -ExecutionPolicy Bypass -File scripts\windows\backup.ps1
# Writes backups\<date-time>\ (contracts.dump + storage\). Copy that folder to
# another disk or cloud drive: a backup on the same PC is lost with the PC.
#
# The encryption key (.env) is deliberately NOT copied here: a backup holding
# both the encrypted SSNs and their key is plain text. Keep .env elsewhere.

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$target = Join-Path (Get-Location) "backups\$stamp"
New-Item -ItemType Directory -Force -Path $target | Out-Null

Write-Host '== Database' -ForegroundColor Cyan
# Written inside the container and copied out: PowerShell's ">" would re-encode the binary dump.
docker compose exec -T postgres pg_dump -U contract_app -d contracts --format=custom -f /tmp/contracts.dump
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
docker compose cp postgres:/tmp/contracts.dump (Join-Path $target 'contracts.dump')
docker compose exec -T postgres rm -f /tmp/contracts.dump | Out-Null

Write-Host '== Files' -ForegroundColor Cyan
docker compose cp backend:/data/storage (Join-Path $target 'storage')
if ($LASTEXITCODE -ne 0) { throw 'Copying the files failed.' }

@'
Restoring (on a fresh install, after setup.ps1 with the SAME .env):

  docker compose cp contracts.dump postgres:/tmp/contracts.dump
  docker compose exec -T postgres pg_restore -U contract_app -d contracts --clean --if-exists /tmp/contracts.dump
  docker compose cp storage\. backend:/data/storage
  docker compose restart backend worker
'@ | Set-Content -Path (Join-Path $target 'RESTORE.txt')

# Keep the newest 14 backups.
Get-ChildItem (Join-Path (Get-Location) 'backups') -Directory | Sort-Object Name -Descending | Select-Object -Skip 14 | Remove-Item -Recurse -Force

Write-Host "Backup written to $target" -ForegroundColor Green
