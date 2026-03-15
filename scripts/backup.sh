#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
BACKUP_ROOT="${REPO_ROOT}/backups"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
HELPER_IMAGE="alpine:3.22"
AWS_CLI_IMAGE="amazon/aws-cli:2.27.41"
VOLUMES=(
  ollama_data
  openwebui_data
  n8n_data
  postgres_data
  traefik_certs
)

log() {
  printf "%s\n" "$1"
}

warn() {
  printf "WARN: %s\n" "$1" >&2
}

fail() {
  printf "ERROR: %s\n" "$1" >&2
}

require_vars() {
  local missing=()
  local key

  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      missing+=("${key}")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    warn "Remote backup is enabled but the following settings are missing: ${missing[*]}"
    return 1
  fi
}

remote_object_key() {
  local prefix="$1"
  local filename="$2"

  if [[ -n "${prefix}" ]]; then
    printf "%s/%s" "${prefix%/}" "${filename}"
  else
    printf "%s" "${filename}"
  fi
}

encrypt_remote_archive() {
  local archive_name="${TIMESTAMP}.tar.gz.enc"
  local archive_path="${BACKUP_ROOT}/${archive_name}"

  if ! command -v openssl >/dev/null 2>&1; then
    fail "Local backup succeeded, but openssl is required for encrypted remote uploads."
    return 1
  fi

  if [[ -z "${BACKUP_PASSPHRASE:-}" ]]; then
    fail "Local backup succeeded, but BACKUP_PASSPHRASE is required when BACKUP_REMOTE_ENABLED=true."
    return 1
  fi

  printf "Creating encrypted remote archive %s\n" "${archive_name}" >&2
  export BACKUP_PASSPHRASE
  tar -czf - -C "${BACKUP_ROOT}" "${TIMESTAMP}" \
    | openssl enc -aes-256-cbc -pbkdf2 -salt \
      -out "${archive_path}" \
      -pass env:BACKUP_PASSPHRASE

  printf "%s" "${archive_path}"
}

upload_s3_archive() {
  local archive_path="$1"
  local archive_name
  local object_key
  local remote_uri
  local command=()

  require_vars BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY || return 1

  archive_name="$(basename "${archive_path}")"
  object_key="$(remote_object_key "${BACKUP_S3_PREFIX:-}" "${archive_name}")"
  remote_uri="s3://${BACKUP_S3_BUCKET}/${object_key}"

  command=(
    docker run --rm
    -e AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY_ID}"
    -e AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_ACCESS_KEY}"
    -e AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}"
    -v "${BACKUP_ROOT}:/backup:ro"
    "${AWS_CLI_IMAGE}"
    s3 cp "/backup/${archive_name}" "${remote_uri}"
  )

  if [[ -n "${BACKUP_S3_ENDPOINT:-}" ]]; then
    command+=(--endpoint-url "${BACKUP_S3_ENDPOINT}")
  fi

  log "Uploading encrypted archive to ${remote_uri}"
  "${command[@]}"
}

upload_b2_archive() {
  local archive_path="$1"
  local archive_name
  local object_key
  local remote_uri
  local command=()

  require_vars BACKUP_B2_BUCKET BACKUP_B2_KEY_ID BACKUP_B2_APPLICATION_KEY BACKUP_B2_ENDPOINT || return 1

  archive_name="$(basename "${archive_path}")"
  object_key="$(remote_object_key "${BACKUP_B2_PREFIX:-}" "${archive_name}")"
  remote_uri="s3://${BACKUP_B2_BUCKET}/${object_key}"

  command=(
    docker run --rm
    -e AWS_ACCESS_KEY_ID="${BACKUP_B2_KEY_ID}"
    -e AWS_SECRET_ACCESS_KEY="${BACKUP_B2_APPLICATION_KEY}"
    -e AWS_DEFAULT_REGION="${BACKUP_B2_REGION:-us-west-002}"
    -v "${BACKUP_ROOT}:/backup:ro"
    "${AWS_CLI_IMAGE}"
    s3 cp "/backup/${archive_name}" "${remote_uri}"
    --endpoint-url "${BACKUP_B2_ENDPOINT}"
  )

  log "Uploading encrypted archive to Backblaze B2 bucket ${BACKUP_B2_BUCKET}"
  "${command[@]}"
}

upload_rsync_archive() {
  local archive_path="$1"
  local ssh_parts=()
  local ssh_command
  local extra_ssh=()

  require_vars BACKUP_RSYNC_TARGET BACKUP_RSYNC_SSH_KEY || return 1

  if ! command -v rsync >/dev/null 2>&1; then
    fail "Local backup succeeded, but rsync is not installed for BACKUP_REMOTE_TYPE=rsync."
    return 1
  fi

  ssh_parts=(
    ssh
    -i "${BACKUP_RSYNC_SSH_KEY}"
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=accept-new
  )

  if [[ -n "${BACKUP_RSYNC_SSH_OPTS:-}" ]]; then
    read -r -a extra_ssh <<< "${BACKUP_RSYNC_SSH_OPTS}"
    ssh_parts+=("${extra_ssh[@]}")
  fi

  printf -v ssh_command '%q ' "${ssh_parts[@]}"
  log "Uploading encrypted archive via rsync to ${BACKUP_RSYNC_TARGET}"
  rsync -av -e "${ssh_command}" "${archive_path}" "${BACKUP_RSYNC_TARGET}"
}

cd "${REPO_ROOT}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

mkdir -p "${BACKUP_DIR}"

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

if [[ "${BACKUP_REMOTE_ENABLED:-false}" =~ ^([Tt][Rr][Uu][Ee]|1|yes|YES|Yes)$ ]]; then
  archive_path=""

  archive_path="$(encrypt_remote_archive)" || exit 1

  case "${BACKUP_REMOTE_TYPE:-}" in
    s3)
      upload_s3_archive "${archive_path}" || exit 1
      ;;
    b2)
      upload_b2_archive "${archive_path}" || exit 1
      ;;
    rsync)
      upload_rsync_archive "${archive_path}" || exit 1
      ;;
    *)
      fail "Local backup succeeded, but BACKUP_REMOTE_TYPE must be one of: s3, b2, rsync."
      exit 1
      ;;
  esac

  printf "remote_archive=%s\n" "$(basename "${archive_path}")" >> "${BACKUP_DIR}/manifest.txt"
  printf "remote_type=%s\n" "${BACKUP_REMOTE_TYPE}" >> "${BACKUP_DIR}/manifest.txt"
fi

printf "\nBackup complete.\n"
printf "Archive folder: %s\n" "${BACKUP_DIR}"
