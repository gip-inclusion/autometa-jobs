#!/usr/bin/env bash
# Shared helpers for autometa-jobs provisioning scripts.

set -euo pipefail

PROJECT_ID="${PIPOMETA_PROJECT_ID:-<scaleway-project-id>}"  # nova
REGION="${PIPOMETA_REGION:-fr-par}"
RDB_INSTANCE_ID="${PIPOMETA_RDB_INSTANCE_ID:-<rdb-instance-id>}"  # proto-db
REGISTRY_NS="${PIPOMETA_REGISTRY_NS:-nova-container-registry}"
BUCKET_NAME="${PIPOMETA_BUCKET:-pipometa}"
DB_NAME="${PIPOMETA_DB_NAME:-pipometa}"

color() { local c=$1; shift; printf '\033[%sm%s\033[0m\n' "$c" "$*" >&2; }
info() { color "1;36" "▸ $*"; }
ok()   { color "1;32" "✓ $*"; }
warn() { color "1;33" "! $*"; }
die()  { color "1;31" "✗ $*"; exit 1; }

confirm() {
  if [[ "${PIPOMETA_AUTO:-0}" == "1" ]]; then
    return 0
  fi
  read -r -p "$1 [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

require_scw() {
  command -v scw >/dev/null || die "scw CLI not on PATH"
}
