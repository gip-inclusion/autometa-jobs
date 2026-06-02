# Ressources

L'infrastructure vivante qu'autometa-jobs possède. Tout dans le projet Scaleway `nova` (`<scaleway-project-id>`), région `fr-par`.

autometa-jobs ne crée et ne modifie jamais que des ressources `pipometa-*`. Les services existants (matometa, fluo, autometa, et autres dans nova) restent intacts.

Les IDs ci-dessous sont scrubés dans ce dépôt public. Valeurs réelles : `RESOURCES.local.md` (gitignoré) et `.env.local` (gitignoré, exporte les IDs en variables `PIPOMETA_*`).

## Compute

| Type | Nom | ID |
|------|-----|-----|
| Serverless Container | `pipometa-orchestrator` | `<orchestrator-container-id>` |
| Container namespace | `nova` | `<container-namespace-id>` |
| Container cron | `pipometa-orchestrator-tick` (chaque minute) | `<orchestrator-tick-cron-id>` |
| Job definition Serverless | `pipometa-worker` | `<worker-job-definition-id>` |

URL orchestrateur : `https://<orchestrator-host>`

Sizing orchestrateur : 250 mvCPU / 512 MiB / `min-scale=1` / `max-scale=1`. Sizing worker : 1 vCPU / 2 GiB / 4 GiB de stockage local / timeout 1h. Les deux sont faciles à retuner via les commandes de gestion dans [CLAUDE.md](CLAUDE.md#déployer-des-changements).

## Stockage

| Type | Nom | Notes |
|------|-----|-------|
| Bucket Object Storage | `pipometa` | Inputs sur `s3://pipometa/inputs/<pipeline>/...`, outputs sur `s3://pipometa/runs/AAAA/MM/JJ/<pipeline>/<run_id>/output.md`. |
| Database Postgres | `pipometa` sur l'instance `proto-db` (`<rdb-instance-id>`) | 4 tables, owner `pipometa_app`. Connexion : `<db-host>:12437`, sslmode=require. |
| Utilisateur Postgres | `pipometa_app` | Non-admin. `permission=all` sur `pipometa` uniquement. |

## Secret Manager

| Nom | ID | Contenu |
|-----|-----|---------|
| `pipometa-claude-oauth-token` | `<secret-oauth-token-id>` | Le token OAuth Claude Max (`sk-ant-oat01-...`). Lu par l'orchestrateur à chaque dispatch, injecté dans l'env du worker. |
| `pipometa-orchestrator-api-key` | `<secret-api-key-id>` | Le bearer token de l'API HTTP de l'orchestrateur. Utilisé par `jobsctl` et tout appelant. |
| `pipometa-db-password` | `<secret-db-password-id>` | Mot de passe de `pipometa_app`. Utilisé pour construire `PIPOMETA_DATABASE_URL`. |

## Container Registry

Espace de registry partagé : `nova-container-registry`.

- `rg.fr-par.scw.cloud/nova-container-registry/pipometa-orchestrator:latest`
- `rg.fr-par.scw.cloud/nova-container-registry/pipometa-worker:latest`

Les deux sont versionnés uniquement par `:latest` en v1. Pour rebuilder, voir [CLAUDE.md](CLAUDE.md#déployer-des-changements).

## Coût (approximatif, mensuel)

| Poste | Coût |
|-------|------|
| Container orchestrateur en `min-scale=1` | ~5 €/mois |
| Runs de Job worker (par run) | ~0,02 € par run de 30 min au-delà du free tier partagé |
| Instance Postgres `proto-db` | 0 € marginal (instance pré-existante) |
| Object Storage, Secret Manager, Cockpit, Registry | centimes |

**Total marginal : ~5–6 €/mois en idle.** Les runs worker sont essentiellement gratuits pour un usage faible volume mono-locataire.
