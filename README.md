# Jodraff Collect

Plateforme d'enquêtes de bout en bout : **choix du service de collecte →
conception du questionnaire → réalisation de l'enquête → gestion des enquêteurs
→ centralisation SQL → traitement et restitution Power Platform → rapport
LaTeX**.


L'architecture de données suit le modèle *medallion* (bronze / silver / gold)
et se déploie sur Azure, tout en restant intégralement exécutable en local.

---

## Le point de départ : le catalogue des services de collecte

Avant toute conception, la plateforme propose les modes de collecte
disponibles. Ce choix n'est pas cosmétique : il conditionne les types de
questions autorisés, la mobilisation ou non d'un réseau d'enquêteurs, le mode
d'alimentation de l'entrepôt et les indicateurs de pilotage.

| Code | Service | Canal | Hors ligne | Enquêteurs | Coût | Durée type | Taux de réponse |
|---|---|---|---|---|---|---|---|
| `CAWI` | Enquête en ligne | Web | — | — | ● | 12 min | 25 % |
| `CAPI` | Face à face sur tablette | Mobile | ✔ | ✔ | ●●●●● | 45 min | 85 % |
| `CATI` | Téléphone | Plateau d'appels | — | ✔ | ●●● | 20 min | 45 % |
| `PAPI` | Papier puis saisie | Papier | — | ✔ | ●●●● | 40 min | 80 % |
| `SMS` | SMS / USSD | Télécom | — | — | ● | 4 min | 18 % |
| `MIXED` | Dispositif multimode | Multi | ✔ | ✔ | ●●●● | 30 min | 70 % |

La page d'accueil expose ce catalogue et un simulateur de charge terrain :

```bash
curl -s localhost:8000/api/v1/collection-services | jq '.services[].name'

curl -s -X POST localhost:8000/api/v1/collection-services/estimate \
  -H 'Content-Type: application/json' \
  -d '{"collection_services": ["CAWI", "CATI"], "sample_size": 1200}'
```

Le simulateur combine les taux de réponse des modes séquencés et renvoie le
nombre de contacts à mobiliser, la charge terrain en heures, les effectifs
d'enquêteurs suggérés et **l'intersection des types de questions supportés** —
car un questionnaire multimode doit se concevoir pour le mode le plus contraint.

---

## Exécuter

Prérequis : **Python 3.11 ou plus**. Rien d'autre pour les quatre premières
étapes — la base et l'entrepôt sont des fichiers locaux. Seul le rapport PDF
demande un moteur LaTeX (étape 4).

> **Windows** : `make`, `source` et `&&` n'existent pas dans PowerShell. Chaque
> commande `make` de ce guide a son équivalent direct, rappelé dans le tableau
> [« Sans make »](#sans-make-windows-ou-machine-sans-make) plus bas.

### 1. Installer

**Linux et macOS**

```bash
git clone https://github.com/JODRAFF9/jodraff-collect
cd jodraff-collect

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/JODRAFF9/jodraff-collect
cd jodraff-collect

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Si PowerShell refuse d'exécuter le script d'activation, autorisez-le pour la
session en cours seulement :

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

L'invite doit ensuite afficher `(.venv)` en tête de ligne. Sans cela, les
paquets s'installent dans le Python global, ce qui fonctionne mais mélange les
projets.

### 2. Créer des données et alimenter l'entrepôt

```bash
make demo
```

Cette commande enchaîne deux choses : elle crée un jeu de démonstration
(enquête multimode CAPI + CATI, questionnaire publié de 12 questions,
9 enquêteurs, 600 unités d'échantillon affectées par zone, 380 entretiens avec
paradonnées et scores de qualité), puis exécute le pipeline complet
bronze → silver → gold → contrôles. Comptez une seconde environ.

### 3. Lancer la plateforme

```bash
make serve
```

| Adresse | Contenu |
|---|---|
| <http://localhost:8000> | Catalogue des services de collecte et simulateur |
| <http://localhost:8000/docs> | Documentation interactive des 42 routes |
| <http://localhost:8000/health> | Sonde de disponibilité |

Comptes de démonstration, mot de passe `motdepasse123` :
`conception@`, `supervision@`, `analyse@`, `admin@jodraff.test`.

Exemple d'appel sans authentification, le catalogue étant public :

```bash
curl -X POST localhost:8000/api/v1/collection-services/estimate \
  -H 'Content-Type: application/json' \
  -d '{"collection_services": ["CAWI", "CATI"], "sample_size": 1200}'
```

### 4. Produire le rapport

```bash
make report SURVEY=ECVM2026
```

Le PDF atterrit dans `var/reports/ECVM2026/`. **Cette étape est la seule qui
compile quelque chose** et demande donc un moteur LaTeX ; sans lui, seul le
`.tex` est produit. Installation et dépannage :
[`reports/README.md`](reports/README.md).

### 5. Vérifier

```bash
make test             # 159 tests
make lint             # contrôle de style
```

### Commandes utiles

```bash
make pipeline         # rejouer bronze → silver → gold
make transform        # rejouer silver et gold sans réextraire
make quality          # contrôles qualité de l'entrepôt seuls
make clean            # supprimer lac, entrepôt et rapports
make reset            # tout remettre à zéro, base comprise
make help             # liste complète
```

### Sans make (Windows, ou machine sans make)

`make` n'est qu'un raccourci. Ces commandes fonctionnent partout, y compris
dans PowerShell :

| Au lieu de | Lancer |
|---|---|
| `make install` | `pip install -e ".[dev]"` |
| `make demo` | `python -m scripts.seed_demo --reset` puis `python -m data_platform.runner run` |
| `make serve` | `python -m uvicorn apps.api.main:app --reload` |
| `make pipeline` | `python -m data_platform.runner run` |
| `make transform` | `python -m data_platform.runner transform` |
| `make quality` | `python -m data_platform.runner quality` |
| `make report` | `python -m reports.generate --survey-code ECVM2026` |
| `make test` | `python -m pytest -q` |
| `make lint` | `python -m ruff check .` |

Toutes se lancent depuis la **racine du dépôt**, celle qui contient
`pyproject.toml`.

> Ne lancez pas `python apps/api/main.py` : ce fichier déclare l'application
> mais ne démarre aucun serveur, et l'exécuter directement casse les imports du
> projet. Le serveur se lance par `uvicorn`, comme ci-dessus.

### Avec VS Code

Le dépôt fournit sa configuration dans `.vscode/` : rien à régler.

**Première ouverture**

1. Ouvrir le dossier du dépôt (*Fichier → Ouvrir le dossier*), pas un fichier
   isolé — sinon les imports du projet ne se résolvent pas.
2. Accepter les extensions recommandées lorsque VS Code les propose.
3. `Ctrl+Maj+P` → **Python: Select Interpreter** → choisir celui de `.venv`.
   C'est l'étape qui conditionne tout le reste.

**Lancer**

`Ctrl+Maj+P` → **Tasks: Run Task**, puis :

| Tâche | Effet |
|---|---|
| Installer les dependances | `pip install -e ".[dev]"` |
| Demarrage complet | Données de test puis alimentation de l'entrepôt |
| API : serveur de developpement | Serveur avec rechargement automatique |
| Pipeline medallion | bronze → silver → gold → contrôles → export |
| Rapport LaTeX | Produit le PDF |
| Tests | Suite complète |

**Déboguer**

Onglet *Exécuter et déboguer* (`Ctrl+Maj+D`) : six configurations, dont
**API (deboguer)** pour poser des points d'arrêt dans les routes.

Cette configuration démarre le serveur **sans** rechargement automatique, à
dessein : le rechargement relance le serveur dans un sous-processus où les
points d'arrêt ne sont plus atteints. Pour développer avec rechargement sans
déboguer, utiliser la tâche *API : serveur de developpement*.

**Tests**

L'explorateur de tests (icône éprouvette) découvre les 159 tests
automatiquement et permet de les lancer un par un, avec débogage.

### Avec Docker

Pour travailler sur PostgreSQL plutôt que SQLite :

```bash
docker compose up --build     # API sur http://localhost:8000
```

---

## Architecture

```
┌─ Couche opérationnelle (OLTP) ──────────────────────────────────────┐
│  Catalogue de collecte  →  Conception  →  Terrain  →  Supervision   │
│  FastAPI + SQLAlchemy · PostgreSQL (Azure) / SQLite (local)         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  extraction par contrats de données
┌──────────────────────────────▼──────────────────────────────────────┐
│  BRONZE   copie fidèle, append-only, Parquet partitionné par date   │
│  SILVER   typé, dédupliqué, conformé, données personnelles masquées │
│  GOLD     schéma en étoile + vues de restitution                    │
│  DuckDB (local) · ADLS Gen2 + Fabric / Synapse (Azure)              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
   Power BI · Power Apps · Power Automate    Rapports LaTeX → PDF
```

Détail complet dans [`ARCHITECTURE.md`](ARCHITECTURE.md).

### Le pipeline, table par table

Chaque couche est produite par des modèles SQL numérotés, exécutés dans
l'ordre. Voici ce qui entre, ce qui en sort, et par quel fichier.

```
BASE OPÉRATIONNELLE (SQLite / PostgreSQL)
│
│   data_platform/contracts.py — 13 contrats de données
│   extraction incrémentale par watermark
▼
BRONZE  var/lake/bronze/<table>/ingest_date=AAAA-MM-JJ/data.parquet
├── surveys              ├── interviews         ├── sample_units
├── questionnaires       ├── answers            ├── enumerators
├── sections             ├── assignments        ├── users
├── questions            ├── paradata           └── quotas
└── choices
│
│   sql/silver/  — déduplication, typage, masquage des données personnelles
▼
SILVER
├── 010_silver_survey.sql ······· silver_survey
├── 020_silver_question.sql ····· silver_question
│                                 silver_choice
├── 030_silver_interview.sql ···· silver_interview      ← + enquêteur, géo, poids
├── 040_silver_answer.sql ······· silver_answer         ← données personnelles masquées ici
└── 050_silver_fieldwork.sql ···· silver_enumerator
                                  silver_assignment
                                  silver_geography
                                  silver_paradata_session
│
│   sql/gold/  — schéma en étoile
▼
GOLD
├── 010_dimensions.sql
│   ├── dim_date ················ générée sur la période de collecte
│   ├── dim_survey
│   ├── dim_collection_service ·· miroir du catalogue d'entrée
│   ├── dim_enumerator
│   ├── dim_question ············ toutes versions de questionnaire
│   ├── dim_geography ··········· avec membre « inconnu »
│   └── dim_interview_status
├── 020_facts.sql
│   ├── fact_interview ·········· grain : un entretien
│   ├── fact_answer ············· grain : une réponse
│   ├── fact_fieldwork_daily ···· grain : enquêteur × jour × mode
│   ├── fact_sample_coverage
│   └── fact_quota
└── 030_marts.sql
    ├── mart_frequency_table ···· tris à plat pondérés
    ├── mart_numeric_summary ···· moyennes, médianes, IC 95 %
    ├── mart_collection_progress
    ├── mart_enumerator_scorecard
    ├── mart_mode_effect ········ écarts entre canaux
    └── mart_quality_overview
│
│   data_platform/quality.py — 18 contrôles
│   ┌── un contrôle bloquant en échec arrête ici : pas d'export
▼
EXPORT  var/lake/gold/*.parquet
│
├──────────────► Power BI · Power Apps · Power Automate
└──────────────► reports/generate.py ──► rapport_*.tex ──► PDF
```

Commandes correspondantes :

| Étape | Commande |
|---|---|
| Base → bronze | `make bronze` |
| Silver + gold + export | `make transform` |
| Chaîne complète | `make pipeline` |
| Contrôles seuls | `make quality` |

### Arborescence

| Répertoire | Contenu |
|---|---|
| `apps/api/` | API opérationnelle : catalogue, conception, collecte, terrain |
| `data_platform/` | Contrats de données, couches bronze/silver/gold, contrôles qualité |
| `warehouse/ddl/` | Schéma T-SQL cible pour Azure SQL et Fabric |
| `powerplatform/` | Modèle sémantique Power BI, mesures DAX, Power Apps, flux |
| `reports/` | Gabarits LaTeX et générateur de rapports |
| `infra/` | Terraform Azure et image conteneur |
| `tests/` | Suite de tests, dont un test de bout en bout |

---

## Les cinq étapes

### 1. Conception du questionnaire

Questionnaires **versionnés et immuables une fois publiés** : toute évolution
crée une version, si bien que chaque entretien reste rattaché à l'instrument
exact qui l'a produit (empreinte `schema_hash`).

La logique du questionnaire — pertinence, contraintes, calculs — s'écrit en
expressions restreintes, évaluées par une liste blanche de l'arbre syntaxique :
ni appel arbitraire, ni accès aux attributs, ni import.

```python
{"code": "revenu_par_tete", "type": "calculate",
 "calculation": "revenu_mensuel / max(taille_menage, 1)"}

{"code": "enfants_scolarises", "type": "single_choice",
 "relevance": "taille_menage > 1"}
```

La validation à la publication bloque notamment : codes inutilisables en SQL,
références à une question posée plus loin, **types incompatibles avec les modes
retenus** (une photo en CATI), sections répétables sans compteur.

### 2. Réalisation de l'enquête

Un moteur unique sert tous les modes ; seule l'enveloppe change. Il assure la
navigation par règles, le contrôle des réponses à la saisie, le recalcul des
variables calculées, les quotas et le score de qualité.

La synchronisation hors ligne (CAPI) est **idempotente par `client_uuid`** : un
lot rejoué après coupure ne crée aucun doublon, et l'échec d'un enregistrement
ne fait pas perdre le reste du lot.

### 3. Gestion des enquêteurs

Habilitations par mode, formation, capacité journalière ; affectation
automatique par proximité géographique puis équilibrage de charge ; transfert
tracé ; tableaux de productivité et de qualité.

Le score de qualité combine durée rapportée à la référence du mode, présence
GPS, taux de remplissage, corrections en cours de saisie et uniformité des
réponses à échelle.

### 4. Centralisation et traitement

```bash
make pipeline    # bronze → silver → gold → export → contrôles
make quality     # contrôles seuls
```

Les **contrats de données** (`data_platform/contracts.py`) fixent le point de
rupture entre l'application et l'entrepôt : la data platform ne lit jamais le
modèle ORM. Le chargement bronze est incrémental par *watermark*.

Les données personnelles sont **masquées en silver** : au-delà, aucune couche
ne manipule de texte identifiant. Un contrôle bloquant le vérifie à chaque
exécution, et un test de bout en bout vérifie qu'aucun nom saisi sur le terrain
n'atteint le rapport diffusé.

Dix-huit contrôles couvrent unicité, non-nullité, intégrité référentielle,
plages de valeurs et cohérence métier. **Si un contrôle bloquant échoue, la
couche gold n'est pas exportée** : Power BI continue d'afficher le dernier état
valide plutôt que des chiffres faux.

### 5. Restitution

**Power BI** consomme le schéma en étoile — voir
[`powerplatform/README.md`](powerplatform/README.md) et les mesures DAX prêtes
à l'emploi (avancement, qualité, productivité, pourcentages pondérés, effet de
mode).

**Les rapports LaTeX** lisent exclusivement la couche gold : aucun recalcul en
Python, donc les chiffres du PDF sont ceux du tableau de bord, par construction.

```bash
make report SURVEY=ECVM2026
```

Prérequis LaTeX et dépannage : [`reports/README.md`](reports/README.md).

Le document produit comporte page de couverture, synthèse à indicateurs,
courbe de collecte comparée à la trajectoire cible, méthodologie, tris à plat
pondérés, statistiques descriptives avec intervalles de confiance, analyse de
l'effet de mode, rendement du réseau, dictionnaire des variables et
traçabilité de l'instrument.

---

## Pondération

Les résultats sont restitués en effectifs bruts **et** en pourcentages
pondérés. Les mesures pondérées (`weighted_usable`, `weighted_answered`,
`Moyenne pondérée`) sont les seules à utiliser pour toute inférence sur la
population : les effectifs bruts servent au pilotage terrain, pas à l'analyse.

En dispositif multimode, la vue `mart_mode_effect` compare les résultats entre
canaux. Un écart marqué doit être analysé **avant** toute publication agrégée :
il peut traduire une différence réelle de population autant qu'un effet du mode
d'administration.

---

## Déploiement Azure

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # puis adapter
terraform init && terraform plan
```

| Ressource | Rôle |
|---|---|
| Container Apps | API opérationnelle, montée en charge sur les requêtes concurrentes |
| PostgreSQL flexible | Base opérationnelle |
| ADLS Gen2 (espace hiérarchique) | Lakehouse medallion, raccordable à OneLake |
| Azure SQL | Couche gold servie à Power BI |
| Data Factory | Orchestration du pipeline |
| Key Vault, Log Analytics | Secrets et observabilité |

Les identités managées et le RBAC remplacent tout secret applicatif ; les
fichiers bronze descendent automatiquement en stockage froid puis archive.

---

## Choix d'implémentation notables

**Réponses stockées en format long.** Une ligne par réponse absorbe sans
migration les évolutions de questionnaire ; la mise à plat en colonnes est
faite en silver, là où elle est rejouable.

**Chargement bronze par fichier CSV de transit.** Le lecteur CSV vectorisé de
DuckDB est environ soixante fois plus rapide que la liaison de paramètres ligne
à ligne : 0,6 s au lieu de 22 s sur le jeu de démonstration.

**Aucune contrainte de clé étrangère sur les faits en cloud.** L'intégrité est
vérifiée par les contrôles du pipeline, ce qui évite qu'un chargement massif
échoue parce qu'une dimension est arrivée en retard.

**Évaluateur d'expressions par liste blanche AST.** Ces expressions sont
saisies par des utilisateurs et exécutées côté serveur : la restriction est une
exigence de sécurité, couverte par des tests dédiés.

---

## Tests

```bash
make test
```

159 tests couvrant le catalogue, l'évaluateur d'expressions (dont les tentatives
d'évasion), la validation des questionnaires, le moteur de collecte, la gestion
du terrain, et un test de bout en bout qui suit une donnée du terrain jusqu'au
rapport en vérifiant qu'aucune donnée personnelle ne fuit en chemin.
