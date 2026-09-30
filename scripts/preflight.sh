#!/usr/bin/env bash
# Check a machine and its .env before the first start.
#
# Everything here is something that otherwise fails later and less clearly: a
# default secret the API refuses to boot with, missing swap that gets the build
# OOM-killed mid-way, a disk with no room for the images.
set -uo pipefail

cd "$(dirname "$0")/.."

PASS=0
WARN=0
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

DEFAULTS="change-me-postgres change-me-minio change-me-admin change-me-in-production
change-me-to-a-long-random-string change-me-to-a-second-long-random-string"

head_ "Tooling"
if command -v docker >/dev/null 2>&1; then
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose plugin present"
  else
    bad "docker compose plugin missing (install docker-compose-plugin)"
  fi
  docker info >/dev/null 2>&1 && ok "docker daemon reachable" \
    || bad "cannot talk to the docker daemon (log out and back in after usermod -aG docker)"
else
  bad "docker is not installed — run scripts/gcp-setup.sh"
fi

head_ "Machine"
TOTAL_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
[ "$TOTAL_MB" -gt 0 ] && ok "RAM: ${TOTAL_MB} MB"
if [ "$TOTAL_MB" -lt 1400 ] && [ "$SWAP_MB" -lt 1024 ]; then
  bad "swap is ${SWAP_MB} MB on a ${TOTAL_MB} MB machine — the image build will be OOM-killed. Run scripts/gcp-setup.sh"
elif [ "$SWAP_MB" -lt 1024 ]; then
  warn "swap is only ${SWAP_MB} MB"
else
  ok "swap: ${SWAP_MB} MB"
fi

DISK_FREE=$(df -Pm . | awk 'NR==2 {print $4}')
if [ "${DISK_FREE:-0}" -lt 6000 ]; then
  bad "only ${DISK_FREE} MB free — images and volumes need roughly 6 GB"
else
  ok "disk free: ${DISK_FREE} MB"
fi

head_ "Configuration"
if [ ! -f .env ]; then
  bad ".env is missing — cp .env.example .env"
else
  ok ".env present"
  # shellcheck disable=SC1091
  set -a; . ./.env 2>/dev/null; set +a

  for name in DB_PASSWORD JWT_SECRET; do
    value="${!name:-}"
    if [ -z "$value" ]; then
      bad "$name is not set"
    elif echo "$DEFAULTS" | grep -qw -- "$value"; then
      if [ "$name" = "JWT_SECRET" ]; then
        bad "$name is still the example value — the API refuses to start with it"
      else
        bad "$name is still the example value — change it before the database is created"
      fi
    elif [ "${#value}" -lt 16 ]; then
      warn "$name is short (${#value} chars); generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
    else
      ok "$name set (${#value} chars)"
    fi
  done

  if [ -z "${PII_ENCRYPTION_KEY:-}" ]; then
    warn "PII_ENCRYPTION_KEY unset — SSNs will be encrypted with JWT_SECRET instead. Workable, but rotating that secret then makes them unreadable"
  elif echo "$DEFAULTS" | grep -qw -- "${PII_ENCRYPTION_KEY}"; then
    bad "PII_ENCRYPTION_KEY is still the example value"
  elif [ "${PII_ENCRYPTION_KEY}" = "${JWT_SECRET:-}" ]; then
    warn "PII_ENCRYPTION_KEY is identical to JWT_SECRET; use two different values"
  else
    ok "PII_ENCRYPTION_KEY set separately from JWT_SECRET"
  fi

  case "${STORAGE_BACKEND:-local}" in
    local) ok "storage backend: local (no MinIO needed)" ;;
    s3)
      if [ "$TOTAL_MB" -lt 1400 ]; then
        bad "STORAGE_BACKEND=s3 on a ${TOTAL_MB} MB machine — MinIO will not fit; use local"
      else
        warn "STORAGE_BACKEND=s3 — start MinIO with: docker compose --profile s3 up -d"
      fi ;;
    *) bad "STORAGE_BACKEND='${STORAGE_BACKEND}' is not a valid value (local|s3)" ;;
  esac

  case "${FRONTEND_BIND:-127.0.0.1}" in
    127.0.0.1|localhost) ok "frontend bound to loopback (reach it over Tailscale/WireGuard)" ;;
    *) warn "FRONTEND_BIND=${FRONTEND_BIND} exposes the UI on a network interface — only do this behind a TLS-terminating proxy" ;;
  esac

  if [ -n "${SEED_ADMIN_EMAIL:-}" ]; then
    if echo "$DEFAULTS" | grep -qw -- "${SEED_ADMIN_PASSWORD:-}"; then
      bad "SEED_ADMIN_PASSWORD is still the example value"
    elif [ "${#SEED_ADMIN_PASSWORD}" -lt 8 ]; then
      bad "SEED_ADMIN_PASSWORD must be at least 8 characters"
    elif [ "${#SEED_ADMIN_PASSWORD}" -gt 72 ]; then
      bad "SEED_ADMIN_PASSWORD exceeds bcrypt's 72-byte limit"
    else
      ok "admin account will be seeded as ${SEED_ADMIN_EMAIL}"
    fi
  else
    warn "SEED_ADMIN_EMAIL unset — no admin will exist, and registration only creates contractors"
  fi

  if [ -f .env ] && git check-ignore -q .env 2>/dev/null; then
    ok ".env is git-ignored"
  else
    bad ".env is NOT git-ignored — your secrets would be committed"
  fi
fi

head_ "Result"
printf '  %d passed, %d warnings, %d blocking\n\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "  Fix the blocking items above before starting."
  exit 1
fi
echo "  Ready. Start with:"
echo "    docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build"
