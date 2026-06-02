# autometa-jobs

Agents autonomes auto-hébergés sur Scaleway. On déclenche un *pipeline* → un container sandboxé éphémère lance l'agent via CLI aussi longtemps que la tâche le demande (jusqu'à 24h) → le résultat atterrit dans S3.

> Mono-locataire par construction. Concurrence 1. Pensé pour un usage solo. Équivalent auto-hébergé de [Claude Managed Agents](https://www.anthropic.com/engineering/claude-managed-agents).

## En deux mots

Deux objets suffisent pour tout comprendre :

- **Pipeline** — une tâche réutilisable : une consigne (le *system prompt*) + une config (outils autorisés, nombre de tours max…). On le définit une fois.
- **Run** — une exécution d'un pipeline. On en déclenche autant qu'on veut ; chacun tourne dans son propre container, isolé des autres, et produit un artefact.

On déclenche un run, on attend, on lit le résultat. C'est tout.

Deux façons de s'en servir, selon que la tâche change ou non d'un run à l'autre :

- **One-shot** — tout est dans la consigne : instructions *et* données. On lance le run « à vide » (`-d '{}'`). Pratique pour une tâche autonome qu'on relance telle quelle.
- **Réutilisable** — la consigne décrit la *méthode* (stable), et on fournit une *entrée* différente à chaque run (un fichier sur S3, via `input_uri`). Même pipeline, données qui varient.

## Avant de commencer

Il faut deux choses, qu'on vous remet une fois :

| Variable | C'est quoi |
|----------|------------|
| `PIPOMETA_URL` | l'adresse de l'orchestrateur (l'API) |
| `PIPOMETA_API_KEY` | votre clé d'accès |

Posez-les dans votre terminal (à refaire à chaque nouveau terminal) :

```sh
export PIPOMETA_URL="https://...."
export PIPOMETA_API_KEY="...."
```

Tous les exemples ci-dessous sont du copier-coller à partir de là. Pas besoin de cloner le dépôt ni d'installer quoi que ce soit pour lancer un job.

## Lancer un job, étape par étape

### 1. Voir les pipelines disponibles

```sh
curl -fsS "$PIPOMETA_URL/pipelines" -H "Authorization: Bearer $PIPOMETA_API_KEY"
```

Vous récupérez une liste ; notez l'`id` du pipeline qui vous intéresse.

### 2. Déclencher un run

```sh
PIPELINE_ID=<collez-l-id-ici>
curl -fsS -X POST "$PIPOMETA_URL/pipelines/$PIPELINE_ID/runs" \
  -H "Authorization: Bearer $PIPOMETA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

La réponse contient l'`id` du run, fraîchement créé. Gardez-le : c'est lui qu'on suit.

> Le `'{}'` ci-dessus lance le pipeline **one-shot** : la tâche entière est dans sa consigne. Pour un pipeline **réutilisable** qui attend une entrée, remplacez `'{}'` par `'{"input_uri": "s3://pipometa/inputs/.../mon-fichier.json"}'` — ce fichier (un JSON avec une clé `prompt`) est ce que l'agent lit comme donnée à traiter. En cas de doute, `'{}'` suffit.

Le run démarre presque toujours dans la seconde (`queued` → `running`). Sinon une tâche de fond le rattrape dans la minute.

### 3. Suivre l'avancement

```sh
RID=<collez-l-id-du-run>

# état d'ensemble (statut, début, fin…)
curl -fsS "$PIPOMETA_URL/runs/$RID" -H "Authorization: Bearer $PIPOMETA_API_KEY"

# déroulé détaillé, événement par événement
curl -fsS "$PIPOMETA_URL/runs/$RID/events" -H "Authorization: Bearer $PIPOMETA_API_KEY"
```

Le champ `status` passe par :

| Statut | Ce que ça veut dire |
|--------|---------------------|
| `queued` | en attente de démarrage |
| `starting` / `running` | en cours |
| `completed` | terminé avec succès ✅ |
| `failed` | échec — voir `error_text` |
| `cancelled` | annulé manuellement |
| `timed_out` | a dépassé la durée max |
| `quota_blocked` | quota momentanément épuisé, à relancer plus tard |

### 4. Lire le résultat

Quand le run est `completed`, deux options :

- **Vite fait** — le champ `summary` de la réponse à l'étape 3 contient le début de l'artefact. Souvent suffisant.
- **En entier** — l'artefact complet est dans S3, à l'adresse donnée par `output_uri`. Si vous avez un accès S3, récupérez-le depuis là.

## Annuler un run

```sh
curl -fsS -X POST "$PIPOMETA_URL/runs/$RID/cancel" -H "Authorization: Bearer $PIPOMETA_API_KEY"
```

L'arrêt est propre : le run sort dès qu'il peut et passe en `cancelled`.

## Plus confortable : la CLI `jobsctl`

Si vous lancez des jobs souvent, le dépôt fournit une petite CLI qui enrobe ces appels. Mêmes deux variables d'environnement, commandes plus courtes :

```sh
jobsctl pipelines              # lister les pipelines
jobsctl trigger <pipeline-id>  # déclencher un run
jobsctl status <run-id>        # voir l'état
jobsctl events <run-id>        # voir le déroulé
jobsctl cancel <run-id>        # annuler
```

Installation : voir [`jobsctl/`](jobsctl/).

## Créer ou modifier un pipeline

Un pipeline, c'est un nom, une consigne et une config. On le crée une fois, puis on déclenche des runs dessus à volonté.

```sh
curl -fsS -X POST "$PIPOMETA_URL/pipelines" \
  -H "Authorization: Bearer $PIPOMETA_API_KEY" -H "Content-Type: application/json" -d "$(cat <<'JSON'
{
  "name": "brief-hebdo",
  "system_prompt": "...votre consigne, voir le template ci-dessous...",
  "config": {
    "scaleway_job_definition_id": "<demandez-cet-id>",
    "max_turns": 20,
    "allowed_tools": ["Bash", "Read", "WebFetch"]
  }
}
JSON
)"
```

- `system_prompt` — la consigne permanente de l'agent (voir le template ci-dessous).
- `allowed_tools` — les outils qu'il a le droit d'utiliser ; liste vide pour tout interdire.
- `scaleway_job_definition_id` — identifiant technique constant, demandez-le une fois à qui gère l'infra.

Pour ajuster un pipeline existant (n'importe quel sous-ensemble de `name` / `system_prompt` / `config`) :

```sh
curl -fsS -X PATCH "$PIPOMETA_URL/pipelines/<pipeline-id>" \
  -H "Authorization: Bearer $PIPOMETA_API_KEY" -H "Content-Type: application/json" \
  -d '{"system_prompt": "..."}'
```

### Template de consigne (`system_prompt`)

C'est le cœur du pipeline : c'est lui qui dicte le comportement de l'agent à chaque run. Une consigne qui marche bien tient en cinq blocs. Copiez ce squelette et remplissez-le :

```text
RÔLE
Tu es <expertise / posture : ex. analyste DDETS, rédacteur de veille>.

OBJECTIF
À chaque exécution, tu dois produire <le livrable précis, en une phrase>.

ENTRÉE
<one-shot> : les données à traiter sont fournies ci-dessous / sont à aller
chercher avec tes outils.
<réutilisable> : tu reçois une entrée différente à chaque run ; traite-la
telle quelle.

MÉTHODE
1. <première étape concrète>
2. <deuxième étape>
3. <…jusqu'au livrable>

FORMAT DE SORTIE
Rends <la structure attendue : Markdown avec ces sections, un tableau,
un JSON avec ces champs…>. <Longueur, ton, langue.>

CONTRAINTES
- <ce qu'il ne faut surtout pas faire>
- <limites : ne pas inventer de chiffres, citer les sources, etc.>
```

Le bloc **ENTRÉE** dépend du mode que vous visez (voir [En deux mots](#en-deux-mots)) :

**Mode one-shot** — instructions *et* données dans la consigne, lancé avec `-d '{}'` :

```text
Tu es analyste DDETS. Produis un constat synthétique sur la question
suivante : « Quelle est l'évolution des CDD d'usage dans l'hôtellerie en
Bretagne sur 2024-2025 ? ». Méthode : recherche, recoupe, rédige. Rends un
Markdown de 300 mots max, structuré en « Constat / Chiffres clés /
Recommandation », en français. N'invente aucun chiffre : si une donnée
manque, dis-le explicitement.
```

**Mode réutilisable** — la consigne décrit la méthode, la question change à chaque run via `input_uri` :

```text
Tu es analyste DDETS. La question à traiter t'est fournie en entrée.
Méthode : recherche, recoupe, rédige. Rends un Markdown de 300 mots max,
structuré en « Constat / Chiffres clés / Recommandation », en français.
N'invente aucun chiffre : si une donnée manque, dis-le explicitement.
```

On déclenche ce second pipeline avec une entrée, où la clé `prompt` porte la question du run :

```sh
# in.json  →  {"prompt": "Évolution des CDD d'usage dans l'hôtellerie en Bretagne 2024-2025 ?"}
curl -fsS -X POST "$PIPOMETA_URL/pipelines/$PIPELINE_ID/runs" \
  -H "Authorization: Bearer $PIPOMETA_API_KEY" -H "Content-Type: application/json" \
  -d '{"input_uri": "s3://pipometa/inputs/ddets/in.json"}'
```

Conseils : une consigne précise sur le **format de sortie** et les **contraintes** donne des artefacts beaucoup plus réguliers d'un run à l'autre. Itérez avec `PATCH` jusqu'à obtenir le rendu voulu.

## Arborescence

```
autometa-jobs/
├── orchestrator/   # l'API : déclenchement, suivi, réconciliation des runs
├── worker/         # ce qui tourne dans le container éphémère + dépose l'artefact
├── jobsctl/        # la CLI : trigger / status / events / cancel
├── infra/          # scripts de provisionnement Scaleway
└── *.md            # doc interne (conception, ressources d'infra)
```
