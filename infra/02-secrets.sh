#!/usr/bin/env bash
# Create / update the secrets needed by the orchestrator.
# Usage: 02-secrets.sh
#
# Reads CLAUDE_CODE_OAUTH_TOKEN from env (preferred) or prompts.
# Generates PIPOMETA_API_KEY if not provided.

source "$(dirname "$0")/lib.sh"
require_scw

OAUTH_SECRET_NAME="${PIPOMETA_OAUTH_SECRET_NAME:-pipometa-claude-oauth-token}"
API_KEY_SECRET_NAME="${PIPOMETA_API_KEY_SECRET_NAME:-pipometa-orchestrator-api-key}"

upsert_secret() {
  local name=$1 data=$2
  local existing_id
  existing_id=$(scw secret secret list project-id="$PROJECT_ID" region="$REGION" -o json \
    | python3 -c "import sys,json; ss=json.load(sys.stdin); print(next((s['id'] for s in ss if s['name']==\"$name\"), ''))")

  if [[ -z "$existing_id" ]]; then
    info "creating secret $name"
    existing_id=$(scw secret secret create name="$name" project-id="$PROJECT_ID" region="$REGION" type=opaque -o json \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
    ok "secret $name -> $existing_id"
  else
    info "secret $name already exists: $existing_id (will append a new version)"
  fi

  local tmp
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' RETURN
  printf '%s' "$data" > "$tmp"
  scw secret version create "$existing_id" data=@"$tmp" region="$REGION" disable-previous=true >/dev/null
  rm -f "$tmp"
  ok "wrote new version of $name"
  printf '%s\n' "$existing_id"
}

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  read -r -s -p "CLAUDE_CODE_OAUTH_TOKEN (input hidden): " CLAUDE_CODE_OAUTH_TOKEN
  echo
fi
[[ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]] || die "OAuth token is empty; aborting"
[[ "$CLAUDE_CODE_OAUTH_TOKEN" == sk-ant-oat01-* ]] \
  || warn "token doesn't look like sk-ant-oat01-... ; continuing anyway"

OAUTH_ID=$(upsert_secret "$OAUTH_SECRET_NAME" "$CLAUDE_CODE_OAUTH_TOKEN")

if [[ -z "${PIPOMETA_API_KEY:-}" ]]; then
  PIPOMETA_API_KEY="pmk_$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  warn "generated PIPOMETA_API_KEY; save this somewhere safe NOW:"
  echo "  $PIPOMETA_API_KEY"
fi
API_KEY_ID=$(upsert_secret "$API_KEY_SECRET_NAME" "$PIPOMETA_API_KEY")

cat <<EOF

next: export the IDs into your deploy env:
  export PIPOMETA_SECRET_OAUTH_TOKEN_ID=$OAUTH_ID
  export PIPOMETA_SECRET_API_KEY_ID=$API_KEY_ID
EOF
