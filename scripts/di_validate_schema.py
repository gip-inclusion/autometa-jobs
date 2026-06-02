#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "tabulate>=0.9"]
# ///
"""Validate the proposed v0.2 schema against the full 92k-service corpus.

For each candidate concept, defines a list of regex patterns. Scans
`publics_precisions`, `conditions_acces`, `description` across the full
parquet and reports:

  - matched_services : services where at least one pattern matched
  - matches_total    : total pattern hits (a service can match multiple)
  - distinct_sources : how many distinct aggregators surface the concept

Runs on the full 92k corpus, not the 300-service discovery sample. Decide
which enum values stay vs. get dropped from v0.2.

Usage: uv run scripts/di_validate_schema.py
Output: data/di/schema_validation.md
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
PARQUET = DATA / "services.parquet"
OUT = DATA / "schema_validation.md"

TEXT_FIELDS = ["publics_precisions", "conditions_acces", "description"]


def i(s: str) -> str:
    """Wrap a literal in a case-insensitive regex group."""
    return f"(?i){s}"


# Each entry: (concept, list of regex). Regex are case-insensitive.
# Be conservative — favour false negatives over false positives at this stage.
# Concepts grouped by where they live in v0.2.
CONCEPTS: dict[str, list[tuple[str, list[str]]]] = {
    # AGE: require an explicit DIRECTION anchor (et plus / minimum / à partir de
    # for min ; moins de / jusqu'à / inclus / limite de for max). Bare "de N
    # ans" is too ambiguous (matches "limite de N ans", "moins de N ans").
    "age": [
        ("age_min: 16",   [r"\b16\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\bà\s*partir\s*de\s*16\s*ans", r"\b16\s*[-–]\s*\d{2}\s*ans", r"\b16\s*à\s*\d{2}\s*ans"]),
        ("age_min: 18",   [r"\b18\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\bà\s*partir\s*de\s*18\s*ans", r"\bplus\s*de\s*18\s*ans", r"\b18\s*[-–]\s*\d{2}\s*ans", r"\b18\s*à\s*\d{2}\s*ans"]),
        ("age_min: 25",   [r"\b25\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\bà\s*partir\s*de\s*25\s*ans"]),
        ("age_min: 26",   [r"\b26\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\bà\s*partir\s*de\s*26\s*ans"]),
        ("age_min: 50",   [r"\b50\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\b\+\s*50\s*ans"]),
        ("age_min: 55",   [r"\b55\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\b\+\s*55\s*ans"]),
        ("age_min: 60",   [r"\b60\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\b\+\s*60\s*ans"]),
        ("age_min: 65",   [r"\b65\s*ans\s*(?:et\s*\+|et\s*plus|minimum|ou\s*plus)", r"\b\+\s*65\s*ans"]),
        ("age_max: 21",   [r"\bmoins\s*de\s*21\s*ans", r"\bjusqu'?à\s*21\s*ans", r"\b21\s*ans\s*(?:inclus|maximum|max\.?)"]),
        ("age_max: 25",   [r"\bmoins\s*de\s*26\s*ans", r"\b\d{2}\s*[-–]\s*25\s*ans", r"\b\d{2}\s*à\s*25\s*ans", r"\bjusqu'?à\s*25\s*ans", r"\b(?:limite|maximum)\s*(?:de\s*)?25\s*ans", r"\bdans\s+la\s+limite\s+de\s+25\s+ans"]),
        ("age_max: 26",   [r"\b\d{2}\s*[-–]\s*26\s*ans", r"\b\d{2}\s*à\s*26\s*ans", r"\bjusqu'?à\s*26\s*ans"]),
        ("age_max: 30",   [r"\bmoins\s*de\s*30\s*ans", r"\bjusqu'?à\s*30\s*ans", r"\b\d{2}\s*[-–]\s*30\s*ans", r"\b\d{2}\s*à\s*30\s*ans"]),
        ("age_sentinelle_ma_boussole", [r"Âge\s+minimum\s*:\s*0\s+Âge\s+maximum\s*:\s*120"]),
    ],

    "situation_familiale": [
        ("parent_isole",                      [r"\bparent\s+isol[ée]"]),
        ("famille_monoparentale",             [r"\bfamille\s+monoparentale", r"\bmonoparental"]),
        ("famille_avec_enfants",              [r"\bfamille[s]?\s+avec\s+enfants?"]),
        ("parent_d_eleve",                    [r"\bparents?\s+d['ée]l[èe]ves?", r"\bparents?\s+d['e]l[ée]ve"]),
        ("famille_en_situation_de_precarite", [r"\bfamille[s]?\s+en\s+situation\s+de\s+pr[ée]carit[ée]"]),
    ],

    "minima_sociaux": [
        ("rsa",                          [r"\bRSA\b", r"Revenu\s+de\s+Solidarit[ée]\s+Active"]),
        ("aah",                          [r"\bAAH\b", r"Allocation\s+(?:aux\s+)?Adultes?\s+Handicap[ée]s?"]),
        ("ass",                          [r"\bASS\b", r"Allocation\s+de\s+Solidarit[ée]\s+Sp[ée]cifique"]),
        ("aspa",                         [r"\bASPA\b", r"Allocation\s+de\s+Solidarit[ée]\s+aux\s+Personnes?\s+[ÂA]g[ée]es?"]),
        ("ata",                          [r"\bATA\b", r"Allocation\s+temporaire\s+d['e]\s*attente"]),
        ("prime_activite",               [r"\bprime\s+d['e]\s*activit[ée]"]),
        ("minima_sociaux_non_specifie",  [r"\bminim?a\s+sociaux\b", r"\bbeneficiaires?\s+(?:des|de)\s+minim"]),
    ],

    # HANDICAP: split adulte vs enfant. The loose "en situation de handicap"
    # was matching "enfant en situation de handicap" (= recipient of crèche
    # service, public being the parent), polluting "personne_situation_handicap".
    # `boe` (Bénéficiaire de l'Obligation d'Emploi) renamed from beneficiaire_oeth.
    # `tutelle_ou_curatelle` moved out into its own concept (not handicap-specific).
    "statut_handicap": [
        ("adulte_situation_handicap",   [r"\b(?:adulte[\.s]?|personne[\.s]?)\s+(?:en\s+)?situation\s+de\s+handicap", r"\bpublic[\.s]?\s+en\s+situation\s+de\s+handicap"]),
        ("enfant_situation_handicap",   [r"\benfant[\.s]?\s+(?:en\s+)?situation\s+de\s+handicap"]),
        ("rqth",                        [r"\bRQTH\b", r"Reconnaissance\s+(?:de\s+la\s+)?Qualit[ée]\s+(?:de\s+)?Travailleur\s+Handicap[ée]"]),
        ("boe",                         [r"\bBOE\b", r"\bb[ée]n[ée]ficiaire[\.s]?\s+de\s+l['e]\s*obligation\s+d['e]\s*emploi"]),
        ("oeth_obligation_employeur",   [r"\bOETH\b", r"obligation\s+d['e]\s*emploi\s+des\s+travailleurs?\s+handicap[ée]s?"]),
        ("invalidite",                  [r"\binvalidit[ée]"]),
        ("pch",                         [r"\bPCH\b", r"Prestation\s+(?:de\s+)?Compensation"]),
    ],

    "protection_juridique": [
        ("tutelle",   [r"\b(?:sous\s+)?tutelle\b"]),
        ("curatelle", [r"\b(?:sous\s+)?curatelle\b"]),
    ],

    # STATUT_PRINCIPAL: "salarie" alone matches structure staff ("nos salariés
    # vous accueillent") so we restrict to constructions where the public is
    # being targeted: "être salarié", "salariés du/des/d'", "public salarié".
    # "etudiant" left loose; cas "étudiant en alternance" sera capté en plus
    # par type_contrat=alternance dans l'extracteur final.
    "statut_principal": [
        ("demandeur_emploi",                    [r"\bdemandeur[\.s]?\s*(?:euse[\.s]?)?\s+d['e]\s*emploi"]),
        ("salarie_strict",                      [r"\b[Êê]tre\s+salari[ée]s?", r"\bsalari[ée]s?\s+(?:du|des|d['e])\s+(?:secteur|entreprise|employeur)", r"\bpublic[\.s]?\s+salari[ée]s?", r"\b(?:vous\s+)?[êe]tes\s+salari[ée]"]),
        ("travailleur_independant",             [r"\btravailleur\s+ind[ée]pendant", r"\bauto[-\s]*entrepreneur"]),
        ("createur_repreneur_entreprise",       [r"\bcr[ée]ateur[\.s]?\s+(?:ou\s+)?repreneur[\.s]?\s+d['e]\s*entreprise", r"\bporteur[\.s]?\s+de\s+projet\s+(?:de\s+)?cr[ée]ation"]),
        ("stagiaire_formation_professionnelle", [r"\bstagiaire\s+(?:de\s+la\s+)?formation\s+professionnelle"]),
        ("volontaire_service_civique",          [r"\bvolontaire\s+(?:du|en)\s+service\s+civique", r"\bservice\s+civique"]),
        ("etudiant",                            [r"\b[ée]tudiant[\.s]?"]),
        ("retraite",                            [r"\b(?:[êe]tre\s+)?retrait[ée][\.s]?\b(?!\s+(?:depuis|de))"]),
    ],

    # DELD/DETLD: longue durée + très longue durée (acronyme TLD).
    "demandeur_emploi_modalite": [
        ("indemnise",       [r"\bdemandeur[\.s]?\s+d['e]\s*emploi\s+indemnis[ée]"]),
        ("non_indemnise",   [r"\bdemandeur[\.s]?\s+d['e]\s*emploi\s+non\s+indemnis[ée]"]),
        ("longue_duree",    [r"\bDELD\b", r"\bDETLD\b", r"\b(?:demandeur[\.s]?\s+d['e]\s*emploi\s+(?:de\s+)?(?:très\s+)?longue\s+dur[ée]e|ch[ôo]meur[\.s]?\s+(?:de\s+)?(?:très\s+)?longue\s+dur[ée]e)", r"\binscrit[\.s]?\s+(?:à|au)\s+P[ôo]le\s+emploi\s+depuis\s+plus\s+de\s+\d+\s+mois"]),
        ("inscrit_n_mois",  [r"\binscrit[\.s]?\s+depuis\s+\d+\s+mois", r"\b\d+\s+mois\s+(?:d['e]\s*)?inscription"]),
        ("contrat_aide",    [r"\bcontrat\s+aid[ée]"]),
    ],

    # TYPE_CONTRAT: deux groupes — "_objectif" pour quand le contrat est ce
    # que le service AIDE A OBTENIR (pas un statut requis), "_statut" pour
    # quand il décrit le public cible. Le post-filtre distingue par contexte
    # ("trouver / décrocher / obtenir / accéder à un CDI" → objectif).
    "type_contrat_statut": [
        ("cdi_statut",          [r"\b(?:[êe]tre\s+(?:en\s+)?(?:un\s+)?|titulaire\s+d['eu]n\s+|salari[ée][\.s]?\s+en\s+|public\s+)CDI\b"]),
        ("cdd_statut",          [r"\b(?:[êe]tre\s+(?:en\s+)?(?:un\s+)?|salari[ée][\.s]?\s+en\s+)CDD\b"]),
        ("alternance_statut",   [r"\b(?:[êe]tre\s+|public\s+|étudiant[\.s]?\s+)?(?:en\s+)?(?:contrat\s+d['e]\s*)?alternan(?:ce|t)[\.s]?", r"\b(?:[êe]tre\s+)?apprenti(?:e?s?)\b", r"\bapprentissage\b"]),
        ("interim_statut",      [r"\bint[ée]rimaire[\.s]?\b", r"\bsalari[ée][\.s]?\s+en\s+int[ée]rim"]),
        ("saisonnier_statut",   [r"\bsalari[ée][\.s]?\s+saisonnier[\.s]?", r"\bcontrat[\.s]?\s+saisonnier", r"\bCDD\s+saisonnier"]),
        ("csp_statut",          [r"\bCSP\b", r"Contrat\s+de\s+S[ée]curisation\s+professionnelle"]),
        ("pec_statut",          [r"\bPEC\b", r"Parcours\s+Emploi\s+Comp[ée]tences"]),
        ("siae_statut",         [r"\bSIAE\b", r"structure[\.s]?\s+d['e]\s*insertion"]),
    ],

    "type_contrat_objectif": [
        ("cdi_objectif",        [r"\b(?:trouver|d[ée]crocher|obtenir|acc[ée]der\s+à|viser|rechercher|chercher|signer|valider)\s+(?:un\s+)?CDI\b", r"\bobjectif\s*:?\s*CDI\b"]),
        ("cdd_objectif",        [r"\b(?:trouver|d[ée]crocher|obtenir|acc[ée]der\s+à)\s+(?:un\s+)?CDD\b"]),
        ("alternance_objectif", [r"\b(?:trouver|signer|d[ée]crocher|chercher|rechercher)\s+(?:un\s+)?(?:contrat\s+(?:d['e]\s*)?)?(?:alternance|apprentissage)"]),
    ],

    "secteur_activite": [
        ("prive_non_agricole",  [r"\bsecteur\s+priv[ée]\s+non\s+agricole", r"\bsalari[ée]s?\s+du\s+secteur\s+priv[ée]"]),
        ("agricole",            [r"\bsecteur\s+agricole", r"\bsalari[ée]s?\s+(?:du\s+)?secteur\s+agricole"]),
        ("public",              [r"\bsecteur\s+public", r"\bfonction\s+publique"]),
        ("spectacle",           [r"\bspectacle\s+vivant", r"\bartiste[\.s]?\s+technicien[\.s]?"]),
    ],

    # majeur: trop de bruit ("défi majeur", "enjeu majeur", "rôle majeur"). Restreindre.
    "situation_administrative": [
        ("primo_arrivant",                                  [r"\bprimo[\s-]*arrivant[\.s]?", r"\bpublic\s+primo"]),
        ("signataire_cir",                                  [r"\bCIR\b", r"Contrat\s+d['e]\s*Int[ée]gration\s+R[ée]publicaine"]),
        ("refugie",                                         [r"\br[ée]fugi[ée][\.s]?\b"]),
        ("beneficiaire_protection_internationale",          [r"\bprotection\s+internationale"]),
        ("beneficiaire_protection_subsidiaire",             [r"\bprotection\s+subsidiaire"]),
        ("beneficiaire_protection_temporaire_ukrainiens",   [r"\bprotection\s+temporaire", r"\bd[ée]plac[ée]s?\s+ukrainiens?"]),
        ("demandeur_asile",                                 [r"\bdemandeur[\.s]?\s+d['e]\s*asile"]),
        ("ressortissant_hors_ue",                           [r"\bhors\s+(?:Union\s+europ[ée]enne|UE)", r"\bressortissant[\.s]?\s+hors"]),
        ("majeur",                                          [r"\b(?:[êe]tre\s+|personne[\.s]?\s+|public[\.s]?\s+|toute\s+personne\s+)majeur(?:e?s?)\b", r"\bmajeur(?:e?s?)\s+ou\s+mineur"]),
        ("mineur_emancipe",                                 [r"\bmineur[\.es]*\s+[ée]mancip"]),
    ],

    "situation_hebergement": [
        ("sans_hebergement",  [r"\bsans\s+h[ée]bergement", r"\bsans[\s-]+abri[\.s]?"]),
        ("heberge",           [r"\b(?:personne[\.s]?\s+)?h[ée]berg[ée]e?s?\b(?!\s+d)"]),
        ("parcours_de_rue",   [r"\bparcours\s+de\s+rue"]),
    ],

    "statut_jeune": [
        ("jeune_sortant_ase",             [r"\bjeune[\.s]?\s+sortant[\.s]?\s+(?:de\s+l['e]\s*)?ASE\b", r"\bAide\s+Sociale\s+[àa]\s+l['e]\s*Enfance", r"\bsortant[\.s]?\s+ASE\b"]),
        ("jeune_descolarise",             [r"\bjeune[\.s]?\s+d[ée]scolaris[ée]"]),
        ("mineur_difficultes_familiales", [r"\bmineur[\.s]?\s+[ée]mancip[ée]\s+ou\s+majeur\s+de\s+moins\s+de\s+21\s+ans", r"\bdifficult[ée]s\s+familiales"]),
        ("etudiant_boursier",             [r"\b[ée]tudiant[\.s]?\s+boursier[\.s]?", r"\bboursier[\.s]?\s+(?:du\s+)?CROUS"]),
    ],

    # `femme` doit exclure "sage-femme". Lookbehind fixe (5 chars).
    "qualificateurs": [
        ("femme",          [r"(?<!sage-)(?<!sage\s)\bfemme[\.s]?\b"]),
        ("senior",         [r"\bs[ée]nior[\.s]?\b", r"\bSeniors?\b"]),
        ("habitant_qpv",   [r"\bQPV\b", r"\bhabitant[\.s]?\s+(?:de\s+|des\s+)?QPV", r"\bquartier[\.s]?\s+prioritaire[\.s]?\s+(?:de\s+)?la\s+ville"]),
    ],

    # IFTR: restreint à "Être inscrit à France Travail" ; "inscrits à Pôle
    # Emploi depuis plus de 12 mois" est sémantiquement DELD, pas IFTR — déplacé.
    # plafond_ressources : split en présence vs absence (bug direction signalé).
    "prerequis_service": [
        ("inscription_france_travail_requise",  [r"\b[Êê]tre\s+inscrit[\.es]?\s+(?:à|au)\s+France\s+Travail", r"\binscription\s+(?:à|au)\s+France\s+Travail\s+(?:obligatoire|requise)"]),
        ("prescription_travailleur_social",     [r"\bprescript(?:ion|eur)\s+(?:par\s+)?(?:un\s+)?travailleur\s+social", r"\borientation\s+par\s+(?:un\s+)?travailleur\s+social"]),
        ("prescription_ofii",                   [r"\bOFII\b", r"Office\s+Fran[çc]ais\s+de\s+l['e]\s*Immigration"]),
        ("plafond_ressources_present",          [r"(?<!sans\s)(?<!ni\s)\bplafond[\.s]?\s+(?:de\s+)?ressources?", r"(?<!sans\s)(?<!ni\s)\bcondition[\.s]?\s+de\s+ressources?", r"\bsous\s+conditions?\s+de\s+ressources?"]),
        ("plafond_ressources_absent",           [r"\bsans\s+conditions?\s+de\s+ressources?", r"\bni\s+(?:de\s+)?conditions?\s+de\s+ressources?", r"\bsans\s+plafond\s+(?:de\s+)?ressources?"]),
        ("revenu_fiscal_par_part_eur",          [r"\brevenu\s+fiscal\s+(?:de\s+r[ée]f[ée]rence)?\s*.{0,40}\bpart", r"\b\d{1,2}\s*\d{3}\s*€\s*(?:par|/)\s*part"]),
        ("mode_accueil_urgence",                [r"\baccueil\s+d['e]\s*urgence", r"\bmise\s+(?:à|a)\s+l['e]\s*abri"]),
    ],

    "prerequis_geographique": [
        ("france_metropolitaine", [r"\bFrance\s+m[ée]tropolitaine"]),
        ("france_hexagonale",     [r"\bFrance\s+hexagonale"]),
        ("drom",                  [r"\bDROM\b", r"\bd[ée]partement[\.s]?\s+et\s+r[ée]gion[\.s]?\s+d['e]\s*outre[-\s]mer"]),
        ("qpv_residence",         [r"\bhabitant[\.s]?\s+(?:de\s+|des\s+)?QPV", r"\br[ée]sider?\s+en\s+QPV"]),
    ],

    # FLE : élargi (alpha, FLI, ASL, illettrisme, lecture-écriture, etc.).
    # Les niveaux CECRL bare (A1, A2, B1, B2) sans le mot "niveau" devant
    # produisent trop de bruit (matricules, codes...) — restreindre.
    "prerequis_niveau_francais": [
        ("infra_a1_1",          [r"\binfra\s*A1\.1", r"\binfra[\s-]a1"]),
        ("a1_1",                [r"\bA1\.1\b"]),
        ("a1_niveau",           [r"\bniveau\s+A1\b"]),
        ("a2_niveau",           [r"\bniveau\s+A2\b"]),
        ("b1_niveau",           [r"\bniveau\s+B1\b"]),
        ("b2_niveau",           [r"\bniveau\s+B2\b"]),
        ("fle",                 [r"\bFLE\b", r"\bfran[çc]ais\s+langue\s+[ée]trang[èe]re"]),
        ("fli",                 [r"\bFLI\b", r"\bfran[çc]ais\s+langue\s+d['e]\s*int[ée]gration"]),
        ("alphabetisation",     [r"\balphab[ée]tisation\b", r"\balpha\b(?:[\s-]+post)?", r"\bpost[\s-]+alpha"]),
        ("illettrisme",         [r"\billettrisme\b", r"\bremise\s+à\s+niveau"]),
        ("asl",                 [r"\bASL\b", r"\batelier[\.s]?\s+sociolinguistique[\.s]?"]),
        ("lecture_ecriture",    [r"\blecture[\s-]+[ée]criture", r"\blire\s+et\s+[ée]crire", r"\bnon[\s-]+lecteur"]),
        ("competences_cles",    [r"\bcomp[ée]tences?\s+cl[ée]s?"]),
    ],
}


def main() -> int:
    if not PARQUET.exists():
        print(f"ERR: {PARQUET} missing", file=sys.stderr)
        return 1

    con = duckdb.connect()

    print(f"Loading services from {PARQUET}...")
    rows = con.execute(
        f"""
        SELECT id, source,
               COALESCE(publics_precisions, '') AS pp,
               COALESCE(conditions_acces, '') AS ca,
               COALESCE(description, '') AS d
        FROM read_parquet('{PARQUET}')
        """
    ).fetchall()
    print(f"  {len(rows):,} services loaded")

    out: list[str] = []
    out.append("# Schema validation — full corpus")
    out.append("")
    out.append(f"Corpus: **{len(rows):,} services**. Patterns case-insensitive.")
    out.append("Per concept: `services` = number of services with at least one pattern hit; `hits` = total pattern matches; `sources` = distinct aggregator sources surfacing the concept.")
    out.append("")

    # Compile patterns once
    compiled: dict[tuple[str, str], list[re.Pattern]] = {}
    for category, items in CONCEPTS.items():
        for concept_name, patterns in items:
            compiled[(category, concept_name)] = [re.compile(p, re.IGNORECASE) for p in patterns]

    # Single pass through the corpus
    counts: dict[tuple[str, str], dict] = {
        k: {"services": 0, "hits": 0, "sources": defaultdict(int), "examples": []}
        for k in compiled
    }
    for service_id, source, pp, ca, d in rows:
        text = f"{pp}\n{ca}\n{d}"
        for k, regs in compiled.items():
            n_hits = 0
            for r in regs:
                n_hits += len(r.findall(text))
            if n_hits > 0:
                c = counts[k]
                c["services"] += 1
                c["hits"] += n_hits
                c["sources"][source] += 1
                if len(c["examples"]) < 2:
                    # find a short literal sample
                    for r in regs:
                        m = r.search(text)
                        if m:
                            ctx = text[max(0, m.start()-20):min(len(text), m.end()+20)]
                            c["examples"].append({"service_id": service_id, "context": ctx.replace("\n", " / ")[:120]})
                            break

    # Render per category
    for category, items in CONCEPTS.items():
        out.append(f"## {category}")
        out.append("")
        rows_table = []
        for concept_name, _ in items:
            c = counts[(category, concept_name)]
            n_svc = c["services"]
            n_hits = c["hits"]
            n_sources = len(c["sources"])
            top_source = max(c["sources"].items(), key=lambda kv: kv[1]) if c["sources"] else ("—", 0)
            ex = c["examples"][0]["context"] if c["examples"] else ""
            rows_table.append([
                concept_name,
                f"{n_svc:,}",
                f"{n_svc/len(rows):.2%}",
                f"{n_hits:,}",
                n_sources,
                f"{top_source[0]} ({top_source[1]})",
                ex[:60],
            ])
        out.append(tabulate(
            rows_table,
            headers=["concept", "services", "share", "hits", "sources", "top source", "example context"],
            tablefmt="github",
        ))
        out.append("")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
