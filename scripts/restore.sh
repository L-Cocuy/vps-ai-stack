#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_ROOT="${REPO_ROOT}/backups"
HELPER_IMAGE="alpine:3.22"
ASSUME_YES=0
BACKUP_DIR=""
VOLUMES=(
  ollama_data
  openwebui_data
  n8n_data
  postgres_data
  traefik_certs
)

usage() {
  printf "Usage: %s [--yes] [backup-folder]\n" "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      BACKUP_DIR="$1"
      shift
      ;;
  esac
done

if [[ -z "${BACKUP_DIR}" && -d "${BACKUP_ROOT}" ]]; then
  BACKUP_DIR="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1)"
fi

if [[ -z "${BACKUP_DIR}" || ! -d "${BACKUP_DIR}" ]]; then
  printf "No backup folder found. Pass a backup path such as backups/20260315-120000\n" >&2
  exit 1
fi

BACKUP_DIR="$(cd "${BACKUP_DIR}" && pwd)"

for volume in "${VOLUMES[@]}"; do
  if [[ ! -f "${BACKUP_DIR}/${volume}.tar.gz" ]]; then
    printf "Missing archive: %s\n" "${BACKUP_DIR}/${volume}.tar.gz" >&2
    exit 1
  fi
done

if [[ ${ASSUME_YES} -ne 1 ]]; then
  printf "This will stop the stack, overwrite the named volumes, and replace .env from the backup.\n"
  printf "Backup source: %s\n" "${BACKUP_DIR}"
  read -r -p "Continue? [y/N] " reply
  if [[ ! "${reply}" =~ ^[Yy]$ ]]; then
    printf "Restore cancelled.\n"
    exit 1
  fi
fi

cd "${REPO_ROOT}"

if ! docker info >/dev/null 2>&1; then
  printf "Docker daemon is not reachable.\n" >&2
  exit 1
fi

printf "Stopping stack...\n"
docker compose down

if [[ -f "${BACKUP_DIR}/.env" ]]; then
  if [[ -f "${REPO_ROOT}/.env" ]]; then
    cp "${REPO_ROOT}/.env" "${REPO_ROOT}/.env.pre-restore.$(date '+%Y%m%d-%H%M%S')"
  fi
  cp "${BACKUP_DIR}/.env" "${REPO_ROOT}/.env"
fi

for volume in "${VOLUMES[@]}"; do
  printf "Restoring volume: %s\n" "${volume}"
  docker volume create "${volume}" >/dev/null
  docker run --rm -v "${volume}:/target" "${HELPER_IMAGE}" \
    sh -c 'rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true'
  docker run --rm \
    -v "${volume}:/target" \
    -v "${BACKUP_DIR}:/backup:ro" \
    "${HELPER_IMAGE}" \
    sh -c "tar -xzf /backup/${volume}.tar.gz -C /target"
done

printf "Restarting stack...\n"
docker compose up -d

printf "\nRestore complete.\n"
printf "Backup source: %s\n" "${BACKUP_DIR}"
