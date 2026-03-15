#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
MIN_FREE_GB=20
FAILURES=0

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[1;33m'
blue='\033[0;34m'
reset='\033[0m'

pass() {
  printf "%b[PASS]%b %s\n" "${green}" "${reset}" "$1"
}

warn() {
  printf "%b[WARN]%b %s\n" "${yellow}" "${reset}" "$1"
}

fail() {
  printf "%b[FAIL]%b %s\n" "${red}" "${reset}" "$1"
  FAILURES=$((FAILURES + 1))
}

info() {
  printf "%b[INFO]%b %s\n" "${blue}" "${reset}" "$1"
}

require_non_default() {
  local key="$1"
  local value="$2"
  local bad_value="$3"
  local hint="$4"

  if [[ -z "${value}" || "${value}" == "${bad_value}" ]]; then
    fail "${key} is not set to a deployment-safe value. ${hint}"
  else
    pass "${key} is set."
  fi
}

printf "VPS AI Stack preflight\n"
printf "Repository: %s\n\n" "${REPO_ROOT}"

if [[ "$(uname -s)" == "Linux" ]]; then
  pass "Linux host detected."
else
  fail "This template is intended for Linux hosts only."
fi

if command -v docker >/dev/null 2>&1; then
  pass "Docker CLI found."
else
  fail "Docker is not installed. Run sudo bash scripts/setup.sh to install it."
fi

if docker info >/dev/null 2>&1; then
  pass "Docker daemon is reachable."
else
  fail "Docker daemon is not reachable. Start Docker or fix permissions before continuing."
fi

if docker compose version >/dev/null 2>&1; then
  pass "Docker Compose plugin found."
else
  fail "docker compose is unavailable. Install the Docker Compose plugin before deploying."
fi

if [[ -f "${ENV_FILE}" ]]; then
  pass ".env file found."
else
  fail ".env is missing. Copy env.example to .env and set real values first."
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a

  require_non_default "DOMAIN" "${DOMAIN:-}" "yourdomain.com" "Set DOMAIN to your apex domain, for example example.com."
  require_non_default "POSTGRES_PASSWORD" "${POSTGRES_PASSWORD:-}" "change_me_strong_password" "Generate a strong password."
  require_non_default "WEBUI_SECRET_KEY" "${WEBUI_SECRET_KEY:-}" "change_me" "Generate one with: openssl rand -hex 32"
  require_non_default "N8N_ENCRYPTION_KEY" "${N8N_ENCRYPTION_KEY:-}" "change_me" "Generate one with: openssl rand -hex 32"
  require_non_default "ACME_EMAIL" "${ACME_EMAIL:-}" "you@yourdomain.com" "Use a real email address for Let's Encrypt notices."

  if [[ -n "${WEBUI_SECRET_KEY:-}" && ${#WEBUI_SECRET_KEY} -lt 32 ]]; then
    fail "WEBUI_SECRET_KEY should be at least 32 characters."
  fi

  if [[ -n "${N8N_ENCRYPTION_KEY:-}" && ${#N8N_ENCRYPTION_KEY} -lt 32 ]]; then
    fail "N8N_ENCRYPTION_KEY should be at least 32 characters."
  fi

  if [[ -n "${DOMAIN:-}" && "${DOMAIN}" != "yourdomain.com" ]]; then
    for host in "chat.${DOMAIN}" "automation.${DOMAIN}"; do
      if resolved="$(getent ahosts "${host}" 2>/dev/null | awk 'NR==1 {print $1}')"; then
        if [[ -n "${resolved}" ]]; then
          pass "${host} resolves in DNS (${resolved})."
        else
          fail "${host} does not resolve yet. Create the DNS record and wait for propagation."
        fi
      else
        fail "${host} does not resolve yet. Create the DNS record and wait for propagation."
      fi
    done
  fi
fi

port_output="$(ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
if [[ -n "${port_output}" ]]; then
  fail "Ports 80 and/or 443 are already in use locally. Stop the conflicting service before deploying."
  printf "%s\n" "${port_output}"
else
  pass "Ports 80 and 443 are available locally."
fi

free_kb="$(df -Pk "${REPO_ROOT}" | awk 'NR==2 {print $4}')"
min_free_kb=$((MIN_FREE_GB * 1024 * 1024))
if [[ -n "${free_kb}" && "${free_kb}" -ge "${min_free_kb}" ]]; then
  pass "Disk check passed with at least ${MIN_FREE_GB} GB free."
else
  fail "Less than ${MIN_FREE_GB} GB free on the target filesystem. Expand disk or clean up space first."
fi

if [[ ${FAILURES} -gt 0 ]]; then
  printf "\n%bPreflight failed with %d issue(s).%b\n" "${red}" "${FAILURES}" "${reset}"
  exit 1
fi

printf "\n%bPreflight passed.%b Safe to continue with scripts/setup.sh.\n" "${green}" "${reset}"
