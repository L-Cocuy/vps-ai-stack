#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
HELPER_IMAGE="alpine:3.22"
CORE_SERVICES=(
  traefik
  postgres
  ollama
  open-webui
  n8n
)
CRITICAL_FAILURES=0
WARNINGS=0

pass() {
  printf "[PASS] %s\n" "$1"
}

warn() {
  printf "[WARN] %s\n" "$1"
  WARNINGS=$((WARNINGS + 1))
}

fail() {
  printf "[FAIL] %s\n" "$1"
  CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
}

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

DISK_WARN_PCT="${HEALTH_DISK_WARN_PCT:-85}"
DISK_FAIL_PCT="${HEALTH_DISK_FAIL_PCT:-95}"

cd "${REPO_ROOT}"

printf "VPS AI Stack health report\n"
printf "Repository: %s\n\n" "${REPO_ROOT}"

if ! command -v docker >/dev/null 2>&1; then
  fail "Docker CLI is not installed."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  fail "Docker daemon is not reachable."
  exit 1
fi

printf "docker compose ps\n"
printf "-----------------\n"
if ! docker compose ps; then
  fail "docker compose ps failed."
  exit 1
fi
printf "\n"

for service in "${CORE_SERVICES[@]}"; do
  container_id="$(docker compose ps -q "${service}" 2>/dev/null || true)"
  if [[ -z "${container_id}" ]]; then
    fail "Service ${service} does not have a container."
    continue
  fi

  status="$(docker inspect -f '{{.State.Status}}' "${container_id}" 2>/dev/null || true)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}" 2>/dev/null || true)"

  if [[ "${status}" != "running" ]]; then
    fail "Service ${service} is not running (status=${status:-unknown})."
    continue
  fi

  case "${health}" in
    healthy|none)
      pass "Service ${service} is running and healthy enough for this template."
      ;;
    starting)
      warn "Service ${service} is still warming up."
      ;;
    unhealthy)
      fail "Service ${service} is running but unhealthy."
      ;;
    *)
      warn "Service ${service} health could not be determined."
      ;;
  esac
done

bootstrap_id="$(docker compose ps -q ollama-bootstrap 2>/dev/null || true)"
if [[ -n "${bootstrap_id}" ]]; then
  bootstrap_exit="$(docker inspect -f '{{.State.ExitCode}}' "${bootstrap_id}" 2>/dev/null || true)"
  bootstrap_status="$(docker inspect -f '{{.State.Status}}' "${bootstrap_id}" 2>/dev/null || true)"
  if [[ "${bootstrap_status}" == "exited" && "${bootstrap_exit}" == "0" ]]; then
    pass "ollama-bootstrap completed successfully."
  elif [[ "${bootstrap_status}" == "running" ]]; then
    warn "ollama-bootstrap is still running. Model downloads may still be in progress."
  else
    fail "ollama-bootstrap did not complete cleanly (status=${bootstrap_status:-unknown}, exit=${bootstrap_exit:-unknown})."
  fi
else
  warn "ollama-bootstrap container was not found."
fi

disk_used_pct="$(df -P "${REPO_ROOT}" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [[ -z "${disk_used_pct}" ]]; then
  warn "Disk usage could not be determined."
elif (( disk_used_pct >= DISK_FAIL_PCT )); then
  fail "Disk usage is ${disk_used_pct}% which exceeds the critical threshold of ${DISK_FAIL_PCT}%."
elif (( disk_used_pct >= DISK_WARN_PCT )); then
  warn "Disk usage is ${disk_used_pct}% which exceeds the warning threshold of ${DISK_WARN_PCT}%."
else
  pass "Disk usage is ${disk_used_pct}%."
fi

if ! docker volume inspect traefik_certs >/dev/null 2>&1; then
  fail "The traefik_certs volume does not exist."
else
  if docker run --rm -v traefik_certs:/certs "${HELPER_IMAGE}" sh -c 'test -f /certs/acme.json'; then
    if docker run --rm -v traefik_certs:/certs "${HELPER_IMAGE}" sh -c 'test -s /certs/acme.json'; then
      pass "Traefik ACME storage exists and contains data."
    else
      warn "Traefik ACME storage exists but acme.json is still empty."
    fi
  else
    fail "Traefik ACME storage exists but /certs/acme.json is missing."
  fi
fi

printf "\nSummary: %d critical, %d warning(s)\n" "${CRITICAL_FAILURES}" "${WARNINGS}"

if (( CRITICAL_FAILURES > 0 )); then
  exit 1
fi
