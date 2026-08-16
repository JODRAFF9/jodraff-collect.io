# Couche Power Platform

La restitution repose sur trois briques Microsoft, branchees sur la **couche
gold** de l'entrepot. Aucune ne recalcule d'indicateur : elles consomment le
schema en etoile produit par le pipeline, ce qui garantit que le tableau de
bord, l'application terrain et le rapport LaTeX affichent les memes chiffres.

| Brique | Role | Public |
|---|---|---|
| Power BI | Pilotage de la collecte, qualite, resultats | Commanditaire, superviseur, analyste |
| Power Apps | Application terrain de secours et validation qualite | Superviseur, enqueteur |
| Power Automate | Alertes, relances, declenchement du pipeline | Automatismes |

---

## 1. Power BI

### Connexion a la source

| Environnement | Source | Mode de stockage |
|---|---|---|
| Local | Dossier Parquet `var/lake/gold/` | Import |
| Azure | Lakehouse Fabric (raccourci OneLake vers ADLS Gen2) | Direct Lake |
| Azure (variante) | Azure SQL Database, schema `gold` | Import + DirectQuery |

Le mode **Direct Lake** de Fabric est recommande : il lit les fichiers Parquet
sans duplication ni rafraichissement planifie, tout en conservant les
performances de l'import.

### Modele semantique

Schema en etoile strict, relations un-a-plusieurs en filtre simple des
dimensions vers les faits :

```
dim_date ──────┐
dim_survey ────┤
dim_enumerator ┼──> fact_interview ──┐
dim_geography ─┤                     ├──> (fact_answer via interview_key)
dim_collection_service ┘             │
dim_question ────────────────────────┘

fact_fieldwork_daily  <── dim_date, dim_enumerator
fact_quota            <── dim_survey
fact_sample_coverage  <── dim_survey, dim_geography
```

Points de vigilance a la creation du modele :

1. **`dim_date` doit etre marquee comme table de dates** (Modelisation →
   Marquer comme table de dates, colonne `date`). Sans cela, les fonctions de
   temporalite (`DATESINPERIOD`, `DATEDIFF`) renvoient des resultats faux.
2. **Sens de filtre simple uniquement.** Le filtrage croise bidirectionnel
   entre `fact_interview` et `fact_answer` cree des ambiguites de chemin et
   fausse les pourcentages ponderes.
3. **Masquer les colonnes techniques** (`*_key`, `date_key`) : elles servent
   aux relations, pas a l'analyse.
4. **`fact_answer` est la table la plus volumineuse.** Au-dela de dix millions
   de lignes, la basculer en DirectQuery ou creer un agregat.

### Mesures

Les mesures sont dans [`powerbi/mesures.dax`](powerbi/mesures.dax), a creer
dans une table `_Mesures` sans colonne. Elles couvrent les volumes,
l'avancement, la qualite, la productivite, les quotas et la mise en forme
conditionnelle.

### Pages de rapport recommandees

| Page | Contenu | Destinataire |
|---|---|---|
| Avancement | Taux de realisation, courbe cumulee contre trajectoire cible, date de fin projetee, avancement par mode et par region | Commanditaire |
| Terrain | Carte des entretiens, charge et rendement par enqueteur, affectations en retard | Superviseur |
| Qualite | Score moyen, entretiens signales, ecart de duree a l'equipe, taux de corrections | Superviseur |
| Quotas | Remplissage par croisement, quotas satures, reste a faire | Superviseur |
| Resultats | Tris a plat ponderes, statistiques descriptives, croisements | Analyste |
| Effet de mode | Ecarts entre canaux, a examiner avant toute publication | Analyste |

### Securite au niveau des lignes

Regle a definir sur `dim_survey` pour cloisonner par organisation, et sur
`dim_enumerator` pour qu'un superviseur ne voie que son equipe :

```dax
// Role « Superviseur »
[supervisor_key] = LOOKUPVALUE (
    dim_enumerator[enumerator_key],
    dim_enumerator[email], USERPRINCIPALNAME ()
)
```

---

## 2. Power Apps

Application canevas connectee a l'API operationnelle par connecteur
personnalise (voir [`powerapps/README.md`](powerapps/README.md)). Elle couvre
deux usages :

- **Validation qualite** : le superviseur parcourt les entretiens signales,
  consulte les paradonnees et valide ou rejette — appels `POST
  /api/v1/interviews/{id}/validate`.
- **Collecte de secours** : saisie d'un entretien lorsque l'application
  terrain est indisponible, via `POST /api/v1/surveys/{id}/interviews` puis
  `POST /api/v1/interviews/{id}/answers/batch`.

Power Apps n'est pas le canal de collecte principal : il ne gere pas le mode
hors ligne prolonge qu'exige le CAPI.

---

## 3. Power Automate

Flux recommandes, decrits dans [`powerautomate/flux.md`](powerautomate/flux.md) :

| Flux | Declencheur | Action |
|---|---|---|
| Rafraichissement | Planifie, toutes les 4 h | `POST /api/v1/analytics/pipeline/run` puis rafraichissement du modele semantique |
| Alerte qualite | Apres pipeline | Si le taux de signalement depasse 10 %, notifier le superviseur (Teams) |
| Alerte retard | Quotidien | Si le taux de realisation est inferieur de 15 points a la trajectoire, alerter le chef de projet |
| Quota sature | Apres pipeline | Fermer la collecte sur le croisement concerne et notifier |
| Rapport hebdomadaire | Lundi 7 h | Declencher la generation LaTeX et diffuser le PDF par courriel |

---

## Ordre d'execution

Le pipeline doit toujours preceder le rafraichissement Power BI :

```
Collecte (API)  →  pipeline medallion  →  controles qualite  →  Power BI  →  rapport LaTeX
```

Si les controles qualite bloquants echouent, le pipeline s'arrete avant
l'export gold : Power BI continue alors d'afficher le dernier etat valide
plutot que des donnees incoherentes.
