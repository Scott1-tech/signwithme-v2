#!/usr/bin/env bash
# Back up the database and the object store to a timestamped directory.
#
# The signed PDFs live in MinIO and the field data lives in Postgres; a backup
# of one without the other restores to a broken system, so both run here or
# neither does.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_ROOT}/${STAMP}"
mkdir -p "${TARGET}"

echo "Backing up Postgres..."
docker compose exec -T postgres pg_dump -U contract_app -d contracts --format=custom \
  > "${TARGET}/contracts.dump"

echo "Backing up object storage..."
docker compose exec -T minio sh -c '
  mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
  mc mirror --quiet local/'"${MINIO_BUCKET:-contracts}"' /tmp/backup >/dev/null &&
  tar -C /tmp -cf - backup
' > "${TARGET}/objects.tar"

# The encryption key is NOT backed up here on purpose: a backup containing both
# the ciphertext and its key is just plaintext in a tarball. Store it separately.
cat > "${TARGET}/RESTORE.md" <<'NOTE'
# Restoring

    docker compose up -d postgres minio
    docker compose exec -T postgres pg_restore -U contract_app -d contracts --clean < contracts.dump
    tar -xf objects.tar && docker compose cp backup minio:/tmp/backup
    docker compose exec minio mc mirror /tmp/backup local/contracts

You also need PII_ENCRYPTION_KEY (or JWT_SECRET if it was never set) from the
deployment's .env. Without it, encrypted SSN and EIN values cannot be read back.
NOTE

echo "Backup written to ${TARGET}"
