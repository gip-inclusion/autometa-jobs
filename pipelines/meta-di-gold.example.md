# Gold-set — exemples annotés (démonstration)

Trois services tirés de `gold_seed.md`, annotés à la main pour illustrer
les trois grandes formes que l'extraction prend en pratique.

> **Workflow :** ce fichier ne sera pas généré automatiquement. Le travail
> consiste à copier `gold_seed.md` → `gold.md`, remplir les blocs YAML, puis
> faire passer un parseur dédié (à écrire) pour produire `gold.jsonl`.

---

## Exemple A — Aucun critère lié au public (mais texte riche)

Service `dora--a9257b56-e9de-4e08-a924-09c0edaa0b55`
*« Accompagnement des propriétaires pour l'amélioration de l'habitat »*

**publics**: _(null)_  
**publics_precisions**: _(null)_  
**conditions_acces**:

> Avoir un logement de plus de 15 ans  
> Etre propriétaire de son logement  
> Avis d'imposition  
> Justificatif de propriété

**Lecture** : la totalité des conditions porte sur le statut **du bien** (propriété, ancienneté, justificatifs), pas sur le public au sens data·inclusion. Aucun champ structurable côté `publics`. Le bon comportement de l'extracteur ici est de tout laisser à `null` et de documenter dans `notes`.

```yaml
age:
  min: null
  max: null
  evidence: null

social_minima: []
social_minima_evidence: []

unemployment:
  min_months: null
  evidence: null

family_situation: []
family_situation_evidence: []

specific_status: []
specific_status_evidence: []

urgency: null
urgency_evidence: null

conflicts: []

notes: |
  Service ciblé propriétaires occupants. Toutes les `conditions_acces` portent
  sur le bien (ancienneté du logement, propriété), pas sur le public.
  À retenir pour le schéma : il existe une catégorie de services dont les
  conditions ne relèvent pas du public mais d'un *statut de bien* ou d'un
  *statut administratif du demandeur* (ex: propriétaire). À discuter avec
  data·inclusion si on veut un champ dédié, ou laisser hors-périmètre.
```

---

## Exemple B — `under_specification` : énumération de sous-catégories

Service `carif-oref--24_1567072`
*« Atelier sociolinguistique - ASL + Cours post alphabétisation »*

**publics**: `personnes-exilees`  
**publics_precisions**: _(null)_  
**conditions_acces**:

> En fonction du niveau d entree souhaite et du niveau d atteinte vise -
> Primo arrivants, y compris refugies et beneficiaires de la protection subsidiaire

**Lecture** : le `publics` normalisé `personnes-exilees` est correct mais sous-spécifie. Le texte explicite trois sous-catégories : *primo-arrivants*, *réfugiés*, *bénéficiaires de la protection subsidiaire*. C'est un cas typique d'`under_specification` à remonter au schéma découvert.

```yaml
age:
  min: null
  max: null
  evidence: null

social_minima: []
social_minima_evidence: []

unemployment:
  min_months: null
  evidence: null

family_situation: []
family_situation_evidence: []

specific_status:
  - primo-arrivant
  - refugie
  - beneficiaire-protection-subsidiaire
specific_status_evidence:
  - value: primo-arrivant
    source_field: conditions_acces
    substring: "Primo arrivants"
  - value: refugie
    source_field: conditions_acces
    substring: "refugies"
  - value: beneficiaire-protection-subsidiaire
    source_field: conditions_acces
    substring: "beneficiaires de la protection subsidiaire"

urgency: null
urgency_evidence: null

conflicts:
  - type: under_specification
    field: publics
    normalized_value: personnes-exilees
    text_evidence: "Primo arrivants, y compris refugies et beneficiaires de la protection subsidiaire"
    explanation: |
      `personnes-exilees` est correct mais trop large. Le texte précise trois
      statuts juridiques distincts qui méritent leur propre énumération dans
      le schéma enrichi.

notes: |
  Le couple (`personnes-exilees` + énumération de statuts juridiques) revient
  probablement souvent dans le corpus. Bon candidat pour un champ
  `statut-administratif-etranger: enum[]` côté `proposed_schema`.
```

---

## Exemple C — Multi-champs structurés (âge + minimum social + statut)

Service `dora--e64d367b-237f-4223-8fe6-09358a8926ca`
*« Atelier Mobilité »*

**publics**: `beneficiaires-des-minimas-sociaux`, `demandeurs-emploi`, `jeunes`  
**publics_precisions**:

> Bénéficiaire du Revenu de Solidarité Active (RSA), Demandeur d'emploi, Public moins de 26 ans

**conditions_acces**: _(null)_

**Lecture** : trois `under_specification` simultanés sur la même ligne de texte.

- `beneficiaires-des-minimas-sociaux` → précisément **RSA**.
- `jeunes` → âge **< 26 ans** (donc `age.max = 25` si on lit strictement « moins de 26 », ou `age.max = 26` si on l'interprète comme « jusqu'à 26 inclus » — à trancher dans le system prompt ; je propose convention stricte = `< X` ⇒ `age.max = X - 1`).
- `demandeurs-emploi` est bien repris tel quel par le texte ; pas d'enrichissement.

```yaml
age:
  min: null
  max: 25
  evidence:
    source_field: publics_precisions
    substring: "moins de 26 ans"

social_minima:
  - RSA
social_minima_evidence:
  - value: RSA
    source_field: publics_precisions
    substring: "Bénéficiaire du Revenu de Solidarité Active (RSA)"

unemployment:
  min_months: null
  evidence: null

family_situation: []
family_situation_evidence: []

specific_status: []
specific_status_evidence: []

urgency: null
urgency_evidence: null

conflicts:
  - type: under_specification
    field: publics
    normalized_value: beneficiaires-des-minimas-sociaux
    text_evidence: "Bénéficiaire du Revenu de Solidarité Active (RSA)"
    explanation: |
      `beneficiaires-des-minimas-sociaux` est trop large : seul RSA est cité.
      AAH/ASS/ASPA/etc. ne sont pas mentionnés.
  - type: under_specification
    field: publics
    normalized_value: jeunes
    text_evidence: "Public moins de 26 ans"
    explanation: |
      `jeunes` est correct mais le texte donne une borne d'âge précise.
      Convention : "moins de 26 ans" ⇒ `age.max = 25` (strict). À fixer dans
      le system prompt.

notes: |
  Cas idéal pour mesurer la qualité d'extraction : trois champs structurés
  remplis depuis une seule phrase. Si l'extracteur en rate un, c'est
  facilement détectable.
```

---

## Patterns observés (à valider sur 17 autres services)

À partir de ces trois cas seulement, on voit déjà émerger des patterns que
`di-discover` devra confirmer :

1. **Tout-null légitime** (Exemple A) — l'évaluation doit récompenser le bon `null`, pas seulement le bon non-null. Risque : un extracteur trop zélé qui invente.
2. **Énumération de sous-catégories** (Exemple B) — typique pour `personnes-exilees`, `personnes-en-situation-de-handicap`, `personnes-en-situation-juridique-specifique`. Schéma : `enum[]` par axe.
3. **Mention multi-axe dans une seule phrase** (Exemple C) — pas rare. L'extracteur doit produire plusieurs `conflicts` depuis le même `text_evidence` partiellement chevauchant.

## Conventions à figer dans le system prompt avant d'annoter les 17 autres

- **« moins de N ans »** ⇒ `age.max = N - 1`. **« jusqu'à N ans »** ⇒ `age.max = N`. **« à partir de N ans »** ⇒ `age.min = N`.
- **`evidence_substring` doit être une sous-chaîne *exacte* du `source_field`** — recopiée caractère par caractère. Si on ne peut pas, le champ est `null`.
- **Une mention peut générer plusieurs `conflicts`** (Exemple C : un seul `publics_precisions` génère 2 `under_specification` distincts).
- **`null` ≠ `[]`** : `null` = pas évalué, `[]` = évalué et rien trouvé. À l'extraction on n'utilise que `[]` pour les listes (pas de `null` sur les listes).
