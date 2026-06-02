# Infra

Bootstrap scripts for autometa-jobs, all idempotent. They reuse existing nova infrastructure where it exists:

- Postgres instance: `proto-db` (already provisioned). autometa-jobs adds a `pipometa` database on it.
- Container Registry: `nova-container-registry` (already provisioned). autometa-jobs pushes `pipometa-orchestrator` and `pipometa-worker` images to it.

What autometa-jobs creates fresh:

- Object Storage bucket `pipometa`
- Secret Manager secret `pipometa-claude-oauth-token`
- Secret Manager secret `pipometa-orchestrator-api-key`
- Serverless Job definition `pipometa-worker`
- Serverless Container `pipometa-orchestrator`

## Run order

1. `01-bootstrap.sh` — bucket + database. Cheap, reversible.
2. `02-secrets.sh <oauth-token>` — writes secrets. Requires the OAuth token from `claude setup-token`.
3. Build and push the worker + orchestrator images (see top-level README).
4. `03-job-definition.sh` — creates the job definition pointing at the worker image.
5. `04-container.sh` — deploys the orchestrator Serverless Container.
6. `05-init-db.sh` — applies the SQL migration.

Each script prints what it would create and asks for confirmation unless `PIPOMETA_AUTO=1` is set.
