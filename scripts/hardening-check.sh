#!/usr/bin/env bash

set -Eeuo pipefail

FAILURES=0
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
  FAILURES=$((FAILURES + 1))
}

normalized_sshd_value() {
  local key="$1"
  local value=""

  if command -v sshd >/dev/null 2>&1; then
    value="$(sshd -T 2>/dev/null | awk -v key="${key}" '$1 == key {print $2}' | tail -n 1)"
  fi

  printf "%s" "${value}"
}

printf "VPS AI Stack hardening audit\n\n"

if [[ "${EUID}" -ne 0 ]]; then
  warn "Run this script with sudo for the most reliable host-level checks."
fi

password_auth="$(normalized_sshd_value passwordauthentication)"
if [[ -z "${password_auth}" ]]; then
  warn "Could not determine the effective SSH PasswordAuthentication setting."
elif [[ "${password_auth}" == "no" ]]; then
  pass "SSH password authentication is disabled."
else
  fail "SSH password authentication is enabled (PasswordAuthentication=${password_auth})."
fi

root_login="$(normalized_sshd_value permitrootlogin)"
if [[ -z "${root_login}" ]]; then
  warn "Could not determine the effective SSH PermitRootLogin setting."
elif [[ "${root_login}" == "no" ]]; then
  pass "Root SSH login is disabled."
else
  fail "Root SSH login is not fully disabled (PermitRootLogin=${root_login})."
fi

if ! command -v ufw >/dev/null 2>&1; then
  fail "UFW is not installed."
else
  ufw_status="$(ufw status 2>/dev/null || true)"
  if [[ "${ufw_status}" == Status:\ active* ]]; then
    pass "UFW is active."

    mapfile -t ufw_rules < <(printf "%s\n" "${ufw_status}" | awk '$2 == "ALLOW" {print $1}' | sort -u)
    unexpected_rules=()
    for rule in "${ufw_rules[@]}"; do
      case "${rule}" in
        22|22/tcp|80|80/tcp|443|443/tcp)
          ;;
        *)
          unexpected_rules+=("${rule}")
          ;;
      esac
    done

    if [[ ${#unexpected_rules[@]} -gt 0 ]]; then
      fail "UFW allows unexpected ports or profiles: ${unexpected_rules[*]}"
    else
      pass "UFW only allows the expected public ports."
    fi

    for required in 22/tcp 80/tcp 443/tcp; do
      required_present=0
      for rule in "${ufw_rules[@]}"; do
        if [[ "${rule}" == "${required}" || "${rule}" == "${required%/tcp}" ]]; then
          required_present=1
          break
        fi
      done

      if [[ "${required_present}" -eq 1 ]]; then
        pass "UFW includes ${required}."
      else
        fail "UFW is missing ${required}."
      fi
    done
  elif [[ -z "${ufw_status}" ]]; then
    warn "Could not read UFW status."
  else
    fail "UFW is not active."
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  if dpkg -s fail2ban >/dev/null 2>&1; then
    if systemctl is-enabled fail2ban >/dev/null 2>&1 && systemctl is-active fail2ban >/dev/null 2>&1; then
      pass "fail2ban is installed and active."
    else
      fail "fail2ban is installed but not enabled and active."
    fi
  else
    fail "fail2ban is not installed."
  fi
else
  warn "systemctl is unavailable, so fail2ban service state could not be verified."
fi

if dpkg -s unattended-upgrades >/dev/null 2>&1; then
  auto_upgrade_enabled="$(grep -Rhs 'APT::Periodic::Unattended-Upgrade "1";' /etc/apt/apt.conf.d 2>/dev/null || true)"
  package_list_updates="$(grep -Rhs 'APT::Periodic::Update-Package-Lists "1";' /etc/apt/apt.conf.d 2>/dev/null || true)"

  if [[ -n "${auto_upgrade_enabled}" && -n "${package_list_updates}" ]]; then
    pass "unattended-upgrades is installed and enabled in APT periodic config."
  else
    fail "unattended-upgrades is installed but not enabled in APT periodic config."
  fi
else
  fail "unattended-upgrades is not installed."
fi

printf "\nSummary: %d fail(s), %d warning(s)\n" "${FAILURES}" "${WARNINGS}"

if (( FAILURES > 0 )); then
  exit 1
fi
