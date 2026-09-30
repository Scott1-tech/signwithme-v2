#!/usr/bin/env bash
# Runs migrations before serving, so a fresh `docker compose up` has a schema.
set -euo pipefail

# The worker container shares this image but must not race the API on migrations.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi

if [ -n "${SEED_ADMIN_EMAIL:-}" ]; then
  python -m app.seed || echo "Seeding skipped."
fi

exec "$@"
