#!/usr/bin/env bash
# Prepare a fresh Google Cloud e2-micro (Debian 12) to run this app.
#
# Run this ON the VM, once, before the first `docker compose up`:
#   curl -fsSL https://raw.githubusercontent.com/<you>/Signwithme1/main/scripts/gcp-setup.sh | bash
# or simply: bash scripts/gcp-setup.sh
#
# e2-micro is a shared-core machine with 1 GB of RAM. Everything below exists
# because of that: without swap, the image build alone will be killed by the
# OOM reaper part-way through, which looks like a mysterious hang.
set -euo pipefail

SWAP_SIZE="${SWAP_SIZE:-4G}"
SWAPFILE=/swapfile

log() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

log "Updating packages"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl git

log "Creating ${SWAP_SIZE} of swap"
if [ -f "${SWAPFILE}" ]; then
  echo "${SWAPFILE} already exists; leaving it alone."
else
  # fallocate is instant; dd is the fallback for filesystems that refuse it.
  sudo fallocate -l "${SWAP_SIZE}" "${SWAPFILE}" 2>/dev/null || \
    sudo dd if=/dev/zero of="${SWAPFILE}" bs=1M count=4096 status=none
  sudo chmod 600 "${SWAPFILE}"
  sudo mkswap "${SWAPFILE}" >/dev/null
  sudo swapon "${SWAPFILE}"
  echo "${SWAPFILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

# Default swappiness of 60 thrashes a small machine; 10 keeps swap as a safety
# net for build spikes rather than a routine destination for hot pages.
sudo sysctl -w vm.swappiness=10 >/dev/null
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null

log "Installing Docker Engine and the compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

log "Capping Docker log growth"
# Without this, JSON logs grow until they fill a 30 GB disk.
sudo mkdir -p /etc/docker
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' | \
  sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker

log "Done"
cat <<'NEXT'
Next steps:

  1. Log out and back in so your shell picks up the docker group.
  2. git clone <your repo> && cd Signwithme1
  3. cp .env.example .env && edit it:
       - DB_PASSWORD, JWT_SECRET, PII_ENCRYPTION_KEY  (generate each with
         python3 -c "import secrets; print(secrets.token_urlsafe(48))")
       - leave STORAGE_BACKEND=local  (MinIO does not fit on this machine)
       - leave FRONTEND_BIND=127.0.0.1 and reach the app over Tailscale
  4. docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build

The first build takes 10-20 minutes on a shared-core instance. If it stalls
during the frontend build, that is memory: confirm swap is active with
`free -h` before assuming anything else is wrong.
NEXT
