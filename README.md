# autometa-jobs

Agents Claude autonomes auto-hébergés sur Scaleway, facturés sur un abonnement Claude Max plutôt qu'au token via l'API. On déclenche un *pipeline* → un container sandboxé éphémère lance la CLI `claude` aussi longtemps que la tâche le demande (jusqu'à 24h) → le résultat atterrit dans S3.

> Mono-locataire par construction. Concurrence 1. Pensé pour un usage solo. Équivalent auto-hébergé de [Claude Managed Agents](https://www.anthropic.com/engineering/claude-managed-agents) pour le cas où on préfère payer l'abonnement plutôt que les tokens.

## Par où commencer

| Vous êtes… | Lisez |
|------------|-------|
| nouvel arrivant, vous voulez l'idée générale | [EXPLAINER.md](EXPLAINER.md) — visite guidée en langage clair |
| en train de travailler sur le code (humain ou agent) | [CLAUDE.md](CLAUDE.md) — onboarding + workflows |
| à la recherche de la conception du système | [ARCHITECTURE.md](ARCHITECTURE.md) — composants, modèle de données, choix de design |
| à la recherche d'un identifiant de ressource | [RESOURCES.md](RESOURCES.md) — l'inventaire vivant |

## État

v1 **vert et bout-en-bout**. Le pipeline `hello` tourne proprement : déclenchement → l'orchestrateur dispatch → le Job Scaleway démarre → la CLI claude tourne avec le prompt du pipeline → les events remontent → l'artefact atterrit sur S3. Environ 20s par run, ~0,005 € avec cache, sous les 6 €/mois en idle.

Ce qui est fait :

- [x] Spec, schéma DB, scripts de provisionnement
- [x] Orchestrateur : API + dispatch piloté par cron + réconciliation
- [x] Worker : CLI claude en direct, non-root, liberté complète sur les outils
- [x] Toutes les ressources Scaleway provisionnées (bucket, DB, secrets, registry, job def, container, cron)
- [x] `system_prompt` et `config_jsonb` par pipeline honorés bout-en-bout

Ce qui vient ensuite :

- [ ] Première charge réelle (à vous de choisir)
- [ ] Webhooks de complétion (v2)
- [ ] Scheduler intégré à l'orchestrateur (la table `schedules`) (v2)

## Arborescence

```
autometa-jobs/
├── orchestrator/   # FastAPI : API, boucle de dispatch, réconciliation
├── worker/         # Image container : runner CLI claude + upload S3
├── jobsctl/        # CLI : trigger / status / events / cancel
├── infra/          # Scripts scw + sql idempotents pour le provisionnement
└── *.md            # Doc (commencer par CLAUDE.md ou EXPLAINER.md)
```
