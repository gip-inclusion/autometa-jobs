#!/usr/bin/env bash
# Deploy the orchestrator as a Serverless Container.
# Requires the orchestrator image to be pushed and the secrets to exist.

source "$(dirname "$0")/lib.sh"
require_scw

CONTAINER_NAME="${PIPOMETA_ORCHESTRATOR_NAME:-pipometa-orchestrator}"
IMAGE="${PIPOMETA_ORCHESTRATOR_IMAGE:-rg.${REGION}.scw.cloud/${REGISTRY_NS}/pipometa-orchestrator:latest}"
NAMESPACE_ID="${PIPOMETA_CONTAINER_NAMESPACE_ID:?set PIPOMETA_CONTAINER_NAMESPACE_ID to the funcscw* namespace}"

info "container: $CONTAINER_NAME"
info "image    : $IMAGE"

# This script intentionally stops short of executing — orchestrator deploy needs
# DATABASE_URL, secret IDs, public_url etc., and is best done through your IaC of
# choice. See the README for the env var list.

cat <<'EOF'
required env (configure on the container):
  PIPOMETA_DATABASE_URL=postgresql+asyncpg://...
  PIPOMETA_API_KEY=...
  PIPOMETA_SCALEWAY_PROJECT_ID=...
  PIPOMETA_SCALEWAY_ACCESS_KEY=...
  PIPOMETA_SCALEWAY_SECRET_KEY=...                  (secret env)
  PIPOMETA_SECRET_OAUTH_TOKEN_ID=...
  PIPOMETA_WORKER_IMAGE=...
  PIPOMETA_PUBLIC_URL=https://...

example create call:
  scw container container create \
    namespace-id=$NAMESPACE_ID \
    name=$CONTAINER_NAME \
    registry-image=$IMAGE \
    port=8080 min-scale=1 max-scale=1 \
    cpu-limit=250 memory-limit=512 \
    privacy=public timeout=300s
EOF
