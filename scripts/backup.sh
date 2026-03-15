#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_ROOT="${REPO_ROOT}/backups"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
HELPER_IMAGE="alpine:3.22"
VOLUMES=(
  ollama_data
  openwebui_data
  n8n_data
  postgres_data
  traefik_certs
)

mkdir -p "${BACKUP_DIR}"

cd "${REPO_ROOT}"

if ! command -v docker >/dev/null 2>&1; then
  printf "Docker is required for backups.\n" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  printf "Docker daemon is not reachable.\n" >&2
  exit 1
fi

printf "Creating backup in %s\n" "${BACKUP_DIR}"

for volume in "${VOLUMES[@]}"; do
  printf "Backing up volume: %s\n" "${volume}"
  docker volume create "${volume}" >/dev/null
  docker run --rm \
    -v "${volume}:/source:ro" \
    -v "${BACKUP_DIR}:/backup" \
    "${HELPER_IMAGE}" \
    sh -c "tar -czf /backup/${volume}.tar.gz -C /source ."
done

if [[ -f "${REPO_ROOT}/.env" ]]; then
  cp "${REPO_ROOT}/.env" "${BACKUP_DIR}/.env"
fi

{
  printf "timestamp=%s\n" "${TIMESTAMP}"
  printf "hostname=%s\n" "$(hostname)"
  printf "repo_root=%s\n" "${REPO_ROOT}"
  printf "helper_image=%s\n" "${HELPER_IMAGE}"
  printf "compose_project=vps-ai-stack\n"
  printf "docker_version=%s\n" "$(docker --version)"
  printf "compose_version=%s\n" "$(docker compose version --short 2>/dev/null || docker compose version)"
  printf "volumes=%s\n" "${VOLUMES[*]}"
} > "${BACKUP_DIR}/manifest.txt"

printf "\nBackup complete.\n"
printf "Archive folder: %s\n" "${BACKUP_DIR}"
