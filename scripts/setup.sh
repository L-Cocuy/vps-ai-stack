#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
TRAEFIK_CERT_VOLUME="traefik_certs"

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[1;33m'
reset='\033[0m'

log() {
  printf "%b==>%b %s\n" "${green}" "${reset}" "$1"
}

warn() {
  printf "%bWARN%b %s\n" "${yellow}" "${reset}" "$1"
}

die() {
  printf "%bERROR%b %s\n" "${red}" "${reset}" "$1"
  exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
  die "Run this script with sudo so it can install Docker when needed."
fi

cd "${REPO_ROOT}"

if ! command -v curl >/dev/null 2>&1; then
  log "Installing curl..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl
fi

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine via the official convenience script..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
else
  log "Docker already installed."
fi

if ! docker compose version >/dev/null 2>&1; then
  log "Installing Docker Compose plugin..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${REPO_ROOT}/env.example" "${ENV_FILE}"
  warn ".env did not exist, so a starter file has been created."
  warn "Edit ${ENV_FILE} with your real domain, email, and secrets, then rerun scripts/setup.sh."
  exit 0
fi

log "Running preflight checks..."
"${SCRIPT_DIR}/preflight.sh"

log "Preparing Traefik certificate storage..."
docker volume create "${TRAEFIK_CERT_VOLUME}" >/dev/null
docker run --rm \
  -v "${TRAEFIK_CERT_VOLUME}:/certs" \
  alpine:3.22 \
  sh -c 'touch /certs/acme.json && chmod 600 /certs/acme.json'

log "Validating Docker Compose configuration..."
docker compose config >/dev/null

log "Pulling pinned images..."
docker compose pull

log "Starting the stack..."
docker compose up -d

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

printf "\n%bStack started.%b\n" "${green}" "${reset}"
printf "Open WebUI: https://chat.%s\n" "${DOMAIN}"
printf "n8n:         https://automation.%s\n" "${DOMAIN}"
printf "\nFirst boot notes:\n"
printf "- Let's Encrypt issuance usually takes 1-3 minutes once DNS is correct.\n"
printf "- Ollama model downloads can take several minutes on the first run.\n"
printf "- Check status with: docker compose ps\n"
printf "- Follow logs with:  docker compose logs -f\n"
