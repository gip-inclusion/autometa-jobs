#!/usr/bin/env bash
# Deploy / update the port-loop driver as a Scaleway Serverless Job with a cron.
#
# The driver (scripts/drive_port_loops.py) is a stdlib-only Python 3 script. It
# is delivered by embedding it, base64-encoded, into the job definition's
# DRIVER_SCRIPT_B64 environment variable -- the job definition IS the
# deployment. There is no image to build and no external code fetch at run
# time, so a crash loses nothing: re-running this script re-syncs everything.
#
# This script is idempotent. Run it to create the job, and re-run it any time
# scripts/drive_port_loops.py changes to push the new code.
#
# The job is created with NO cron schedule -> it is DISABLED (it never fires
# on its own). Enable it with the command this script prints at the end.
#
# Secrets (orchestrator API key, GitHub PAT) are injected as secret references
# from Scaleway Secret Manager -- they are never written to disk or echoed.
#
# Requires: scw CLI authenticated, and autometa-jobs/.env.local sourced (or present
# next to this script's parent dir).
#
# Usage:
#   bash deploy_port_driver_cron.sh            # deploy/update, live mode
#   bash deploy_port_driver_cron.sh --dry-run  # deploy/update with DRIVER_DRY_RUN=1
#
# IMPORTANT: `scw jobs definition update environment-variables.X=Y` REPLACES
# the whole env map, it does not merge. This script therefore always writes the
# complete env in a single call. Never run a partial `environment-variables.*`
# update against this job by hand -- it will wipe DRIVER_SCRIPT_B64. To toggle
# dry-run, re-run this script with/without --dry-run.

set -euo pipefail

DRY_RUN_FLAG=""
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN_FLAG="environment-variables.DRIVER_DRY_RUN=1"
  echo "mode: DRY-RUN (driver will decide + log but trigger nothing)"
else
  echo "mode: LIVE (driver will trigger loop runs)"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
DRIVER="$HERE/drive_port_loops.py"

# ---- config ---------------------------------------------------------------

REGION="${PIPOMETA_REGION:-fr-par}"
PROJECT_ID="${PIPOMETA_PROJECT_ID:?source .env.local first (PIPOMETA_PROJECT_ID unset)}"
ORCH_URL="${PIPOMETA_URL:?source .env.local first (PIPOMETA_URL unset)}"

JOB_NAME="pipometa-port-driver"
JOB_IMAGE="python:3.12-slim"
CRON_SCHEDULE="*/30 * * * *"   # every 30 minutes -- applied only on --enable

# Secret Manager IDs (stable; from .env.local / RESOURCES.md).
SECRET_API_KEY_ID="${PIPOMETA_SECRET_API_KEY_ID:-<secret-api-key-id>}"
SECRET_REPO_PAT_ID="${PIPOMETA_SECRET_REPO_PAT_ID:-<secret-repo-pat-id>}"

# ---- driver payload -------------------------------------------------------

[ -f "$DRIVER" ] || { echo "driver not found: $DRIVER" >&2; exit 1; }
DRIVER_B64="$(base64 < "$DRIVER" | tr -d '\n')"
echo "driver: $DRIVER ($(wc -c < "$DRIVER" | tr -d ' ') bytes, b64 ${#DRIVER_B64})"

# The startup command: decode the embedded driver and exec it. Self-contained,
# no network needed to obtain the code itself.
BOOTSTRAP='import base64,os;exec(compile(base64.b64decode(os.environ["DRIVER_SCRIPT_B64"]),"drive_port_loops.py","exec"))'

# ---- find or create the job definition ------------------------------------

JOB_ID="$(scw jobs definition list region="$REGION" project-id="$PROJECT_ID" -o json 2>/dev/null \
  | python3 -c "import sys,json;[print(d['id']) for d in json.load(sys.stdin) if d['name']=='$JOB_NAME']" || true)"

if [ -n "$JOB_ID" ]; then
  echo "updating existing job definition $JOB_ID"
  scw jobs definition update "$JOB_ID" region="$REGION" \
    image-uri="$JOB_IMAGE" \
    cpu-limit=250 memory-limit=512 local-storage-capacity=1024 \
    job-timeout=900s \
    startup-command.0="python3" startup-command.1="-c" startup-command.2="$BOOTSTRAP" \
    environment-variables.DRIVER_SCRIPT_B64="$DRIVER_B64" \
    environment-variables.PIPOMETA_URL="$ORCH_URL" \
    environment-variables.PORT_REPO="louije/rdv-insertion" \
    environment-variables.PORT_BRANCH="claude/plan-django-rewrite-RaF3g" \
    environment-variables.DRIVER_CONCURRENCY="3" \
    $DRY_RUN_FLAG \
    -o json > /dev/null
else
  echo "creating job definition $JOB_NAME (no cron schedule -> DISABLED)"
  JOB_ID="$(scw jobs definition create region="$REGION" project-id="$PROJECT_ID" \
    name="$JOB_NAME" \
    image-uri="$JOB_IMAGE" \
    cpu-limit=250 memory-limit=512 local-storage-capacity=1024 \
    job-timeout=900s \
    description="Autonomous driver for the RDV-Insertion port loops (L0-L4). Cron-scheduled; created disabled." \
    startup-command.0="python3" startup-command.1="-c" startup-command.2="$BOOTSTRAP" \
    environment-variables.DRIVER_SCRIPT_B64="$DRIVER_B64" \
    environment-variables.PIPOMETA_URL="$ORCH_URL" \
    environment-variables.PORT_REPO="louije/rdv-insertion" \
    environment-variables.PORT_BRANCH="claude/plan-django-rewrite-RaF3g" \
    environment-variables.DRIVER_CONCURRENCY="3" \
    $DRY_RUN_FLAG \
    -o json | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")"
  echo "created job definition $JOB_ID"
fi

# ---- secret references (idempotent) ---------------------------------------
# Inject the orchestrator API key and GitHub PAT as env vars sourced from
# Secret Manager. Delete-then-create keeps it idempotent across re-runs.

scw jobs secret list job-definition-id="$JOB_ID" region="$REGION" -o json 2>/dev/null \
  | python3 -c "import sys,json;d=json.load(sys.stdin);[print(s['secret_id']) for s in d.get('secrets',d if isinstance(d,list) else [])]" 2>/dev/null \
  | while read -r SID; do
      [ -n "$SID" ] && scw jobs secret delete secret-id="$SID" region="$REGION" >/dev/null 2>&1 || true
    done

scw jobs secret create job-definition-id="$JOB_ID" region="$REGION" \
  secrets.0.secret-manager-id="$SECRET_API_KEY_ID" \
  secrets.0.secret-manager-version="latest" \
  secrets.0.env-var-name="PIPOMETA_API_KEY" \
  secrets.1.secret-manager-id="$SECRET_REPO_PAT_ID" \
  secrets.1.secret-manager-version="latest" \
  secrets.1.env-var-name="GIT_PAT" \
  -o json > /dev/null
echo "secret references set: PIPOMETA_API_KEY, GIT_PAT"

# ---- report ---------------------------------------------------------------

cat <<EOF

================================================================
port-driver job definition: $JOB_ID   (name: $JOB_NAME)
state: DISABLED -- no cron schedule attached, it will not fire.

ENABLE (attach the 30-minute cron):
  scw jobs definition update $JOB_ID region=$REGION \\
    cron-schedule.schedule="$CRON_SCHEDULE" cron-schedule.timezone="Europe/Paris"

DISABLE (detach the cron -- the job stops firing):
  scw jobs definition update $JOB_ID region=$REGION cron-schedule.schedule=""

RUN ONCE NOW (manual test, no schedule needed):
  scw jobs definition start $JOB_ID region=$REGION

DRY-RUN MODE (decide + log, trigger nothing) -- re-deploy with the flag:
  bash scripts/deploy_port_driver_cron.sh --dry-run
  (back to live: bash scripts/deploy_port_driver_cron.sh)
  Do NOT toggle it with a bare 'environment-variables.*' update -- that REPLACES
  the whole env map and wipes DRIVER_SCRIPT_B64.

INSPECT a run (NB: 'scw jobs run get' prints env incl. secrets -- handle as sensitive):
  scw jobs run list job-definition-id=$JOB_ID region=$REGION
================================================================
EOF
