#!/usr/bin/env bash
# Create the Serverless Job definition for pipometa-worker.
# Requires the worker image to be pushed to the registry.

source "$(dirname "$0")/lib.sh"
require_scw

JOB_NAME="${PIPOMETA_JOB_NAME:-pipometa-worker}"
IMAGE="${PIPOMETA_WORKER_IMAGE:-rg.${REGION}.scw.cloud/${REGISTRY_NS}/pipometa-worker:latest}"
CPU_MVCPU="${PIPOMETA_WORKER_CPU:-1000}"        # 1 vCPU
MEMORY_MB="${PIPOMETA_WORKER_MEM:-2048}"        # 2 GB
LOCAL_STORAGE_MB="${PIPOMETA_WORKER_LOCAL_STORAGE:-4096}"  # 4 GB scratch
TIMEOUT_S="${PIPOMETA_WORKER_TIMEOUT_S:-3600}"

OAUTH_SECRET_NAME="${PIPOMETA_OAUTH_SECRET_NAME:-pipometa-claude-oauth-token}"

info "job definition: $JOB_NAME"
info "image         : $IMAGE"
info "resources     : ${CPU_MVCPU} mvCPU / ${MEMORY_MB} MiB / ${TIMEOUT_S}s timeout"

existing_id=$(scw jobs definition list project-id="$PROJECT_ID" region="$REGION" -o json \
  | python3 -c "import sys,json; jj=json.load(sys.stdin); print(next((j['id'] for j in jj if j['name']==\"$JOB_NAME\"), ''))")

if [[ -n "$existing_id" ]]; then
  ok "job definition $JOB_NAME already exists: $existing_id"
  exit 0
fi

if ! confirm "Create job definition $JOB_NAME?"; then
  warn "skipped"; exit 0
fi

scw jobs definition create \
  name="$JOB_NAME" \
  project-id="$PROJECT_ID" \
  region="$REGION" \
  image-uri="$IMAGE" \
  cpu-limit="$CPU_MVCPU" \
  memory-limit="$MEMORY_MB" \
  local-storage-capacity="$LOCAL_STORAGE_MB" \
  job-timeout="${TIMEOUT_S}s" \
  description="autometa-jobs worker — runs Claude Agent SDK pipelines"

ok "job definition $JOB_NAME created"
warn "remember to attach the OAuth token as a secret env var via the console or scw API."
warn "(scw CLI doesn't yet expose 'secret_environment_variables' on jobs definitions; the orchestrator"
warn " injects it at run start anyway, so this is informational.)"
