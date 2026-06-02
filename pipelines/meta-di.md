# Pipeline normalisation des services d'insertion

## Contexte

Les services d'insertion publiés sur data.gouv.fr (dataset
[`6233723c2c1e4a54af2f6b2d`](https://www.data.gouv.fr/api/1/datasets/6233723c2c1e4a54af2f6b2d/))
sont normalisés au schéma data·inclusion v1
(<https://gip-inclusion.github.io/data-inclusion-schema/latest/>).

Deux référentiels manquent de précision :

- **`publics`** — les catégories d'âge, situation familiale, urgence et minima sociaux sont trop larges.
- **`conditions_acces`** — même problème.

La granularité réelle (âges précis, listes exactes de minima, durée d'inscription
au chômage, statuts spécifiques…) vit dans le champ libre `publics_precisions`
et dans `description`.

L'objectif est double :

1. **Découvrir** un vocabulaire enrichi pour ces deux référentiels à partir du texte libre.
2. **Extraire** ce vocabulaire de manière structurée, en citant la sous-chaîne
   qui justifie chaque valeur, et **signaler** les écarts entre champs normalisés
   et texte libre.

## Source de données

- Fichier : **parquet** (le plus petit, ~50 Mo, requêtable directement avec duckdb).
- Téléchargement et préparation : script déterministe `scripts/di_prep.py` —
  pas un pipeline d'agent.
- Sortie sur S3 : `s3://pipometa/inputs/di/<batch_id>/{sample.parquet, manifest.json}`.

## Phase 0 — Profilage préalable

Avant de figer un sampling, on a besoin d'ordres de grandeur sur le corpus.
Tant qu'on ne sait pas si `publics_precisions` est non-vide pour 5 % ou 60 %
des services, les quotas d'échantillonnage sont des hypothèses creuses.

Script : `scripts/di_profile.py`. Mesures :

- **Remplissage par champ** (`publics`, `publics_precisions`, `conditions_acces`,
  `description`) : % null / vide / whitespace / non-vide. Pour les non-vides,
  distribution de longueur (médiane, p25, p75, p95).
- **Nombre de valeurs** dans `publics` (1, 2, 3+) quand non-vide.
- **Croisements** : `publics` (vide / partiel / dense) × `publics_precisions`
  (vide / non-vide), avec `description ≥ 200 chars` comme troisième axe.
- **Distribution par `source`** (l'aggregator d'origine), avec part de chacun
  dans le corpus. Conditionne le plafond par source dans le sampling.
- **Distribution par `structure.type`** si disponible.

Sortie : `data/di/profile.md` (chiffres + tableaux) — relu humainement avant
de figer les quotas définitifs.

## Phase 1 — Échantillonnage

**Corpus réel** (constaté au profilage) : 92 179 services, pas 160 000.

Stratification à un axe, pondération à deux facteurs, **seed fixe**
(`20260504`) pour reproductibilité.

### Axe — richesse du texte libre

Définition (révisée après profilage : `description` est rempli pour 100 %
des services et ne discrimine rien) :

- **Riche** : `publics_precisions` non-vide *ou* `conditions_acces` non-vide.
- **Normalisé sans texte** : pas de texte libre, mais `publics` non-vide.
- **Vide partout** : aucun des trois rempli.

### Pondération intra-stratum

Au sein de chaque stratum, on tire sans remplacement avec pondération :

```
weight(row) = (1 / source_count(row.source) ** α) * score_boost(row.score_qualite)
```

- **`α = 0.5`** : neutralise partiellement les sources dominantes (DORA = 22 %)
  sans les exclure. À 0 : tirage proportionnel ; à 1 : équiprobabilité par source.
- **`score_boost`** : booste les services bas-score, qui sont rares (12 % du
  corpus < 0.75) mais portent du signal sur la qualité d'extraction :
  - `score < 0.50` → boost ×2
  - `0.50 ≤ score < 0.75` → boost ×1.5
  - `score ≥ 0.75` → boost ×1

### Quotas

| Stratum              | Part  | n (sur 1500) | n (sur 300) | Logique                                                  |
|----------------------|-------|--------------|-------------|----------------------------------------------------------|
| Riche en texte libre | 65 %  | 975          | 195         | Cible principale (vocabulaire enrichi vit ici)           |
| Normalisé sans texte | 20 %  | 300          | 60          | Contrôle : l'extracteur doit majoritairement renvoyer null |
| Vide partout         | 15 %  | 225          | 45          | Adversarial : pas de texte libre, normalisé vide aussi   |

### Tailles

- **300 services** pour la découverte (`di-discover`).
- **1500 services** pour la validation de l'extraction (`di-extract`),
  **sur-ensemble strict** des 300 précédents.

Rationale : la saturation de vocabulaire arrive bien avant 1600 services ;
au-delà de ~300 le retour décroît vite. Le reste du budget paie la validation.

### Note sur le stratum « vide partout »

Le profilage l'a chiffré à 1,5 % du corpus (~1 386 services). Le quota cible
de 15 % est donc fortement sur-représenté. C'est volontaire : on veut savoir
si l'extracteur tient quand il n'a aucun signal.

## Phase 2 — Gold set

Avant d'évaluer la qualité de l'extraction, on annote à la main une vingtaine de
services tirés du sample, pour en faire un jeu de référence. Sans gold set,
"ça marche" n'est pas mesurable.

- Template d'annotation généré par `scripts/di_gold_seed.py` →
  `data/di/gold_seed.md` (gitignoré, regénérable, seed déterministe).
- Exemples annotés (référence pour qui annote) :
  [`meta-di-gold.example.md`](meta-di-gold.example.md) — trois services
  illustrant les trois shapes principales (tout-null légitime,
  énumération de sous-catégories, multi-champs depuis une phrase).
- Annotation effective : `data/di/gold.md` (jamais commité).
- Sortie machine : `data/di/gold.jsonl` (jamais commité).

## Phase 3 — Pipeline `di-discover`

Définition runtime : `pipelines/di-discover.json`.

**Input** : URI du `manifest.json`.

**Tâche** : pour chaque service du sample, identifier les mentions
non-structurées d'âges, situations familiales, minima sociaux, durées
d'inscription au chômage, statuts ; les regrouper en catégories ; produire
un schéma enrichi et un comptage de valeurs.

**Output** sur `s3://pipometa/runs/<run_id>/` :

- `proposed_schema.json` — fragment JSON-Schema des champs ajoutés sous
  `publics` et `conditions_acces`. Contient `version` et `discovered_from_batch`.
- `value_tally.json` — chaque valeur découverte avec sa fréquence et ≥ 3 exemples
  de sous-chaînes sources.
- `discovery_notes.md` — synthèse pour relecture humaine.

## Phase 4 — Pipeline `di-extract`

Définition runtime : `pipelines/di-extract.json`.

**Input** : `{schema_uri, batch_uri}`.

**Tâche** : pour chaque service, remplir les champs du schéma découvert.
Chaque scalaire extrait est un objet
`{value, source_field, evidence_substring}` — pas de valeur nue.
Si le texte ne le dit pas, le champ reste `null` (politique anti-hallucination
explicite dans le system prompt, exemples négatifs inclus).

**Conflits** — deux types, étiquetés distinctement :

- `under_specification` — le texte précise une valeur normalisée trop large.
  L'extracteur ajoute la précision sans modifier le champ normalisé.
- `contradiction` — le texte contredit la valeur normalisée. L'extracteur
  pose un flag, ne réécrit pas.

**Output** : JSONL, un record par service, contenant la version du schéma
utilisée. Batches de 30–50 services par tour d'agent.

## Décisions verrouillées

- **Format** : parquet, lu via duckdb.
- **Sampling** : seedé (`20260504`), reproductible, stratifié.
- **Évidence requise** : tout scalaire extrait porte `{value, source_field, evidence_substring}` ou est `null`. Pas de valeur nue.
- **Politique anti-hallucination** : si le texte ne le dit pas, le champ est `null`. Exemples négatifs explicites dans le system prompt.
- **Versioning** : `proposed_schema.json` porte `version` et `discovered_from_batch`. Chaque record d'extraction porte la version du schéma utilisée.
- **Déterminisme** : `temperature=0` pour l'extraction. Discovery peut tolérer un peu de variance.

## Cadence

- `di-prep` + `di-profile` : à la main, à chaque nouveau dump data.gouv.fr.
- `di-discover` : rare. Première fois sur un nouveau référentiel ou drift suspecté. Sortie relue humainement avant de figer la version du schéma.
- `di-extract` : incrémental, batchs, contre une version de schéma figée. Idempotent.

## Où vit quoi

| Type                                  | Emplacement                                |
|---------------------------------------|--------------------------------------------|
| Spec lisible (ce fichier)             | `pipelines/meta-di.md`                     |
| Définition runtime des pipelines      | `pipelines/di-{discover,extract}.json`     |
| Définition opérationnelle (effective) | Postgres, via `POST /pipelines`            |
| Scripts déterministes                 | `scripts/di_{prep,profile}.py`             |
| Données (parquet, samples, schémas)   | S3 (`s3://pipometa/inputs/di/...`, runs)   |
| Données locales transitoires          | `data/di/` (gitignored)                    |
| Gold set                              | `data/di/gold.jsonl` (jamais commité)      |
