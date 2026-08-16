# Architecture

Ce document explique **pourquoi** la plateforme est structurée ainsi. Le
fonctionnement pas à pas est dans le [README](README.md).

---

## Principe directeur : une seule source de vérité par étape

Une plateforme d'enquête échoue rarement sur la technique. Elle échoue quand le
chiffre du tableau de bord ne correspond pas à celui du rapport, quand personne
ne sait plus quelle version du questionnaire a produit telle donnée, ou quand
un nom de répondant se retrouve dans un fichier diffusé.

Trois décisions structurantes répondent à ces trois risques :

1. **Le questionnaire publié est immuable.** Chaque entretien porte l'identifiant
   de sa version et son empreinte. Une donnée collectée reste interprétable des
   années plus tard avec l'instrument exact qui l'a produite.
2. **Tout ce qui est restitué vient de la couche gold.** Power BI et le
   générateur LaTeX lisent les mêmes tables. Aucun recalcul n'existe dans
   l'outil de restitution, donc aucun écart n'est possible entre les deux.
3. **Les données personnelles sont neutralisées en silver.** Le masquage n'est
   pas une option de diffusion, c'est une étape du pipeline. Un contrôle
   bloquant et un test de bout en bout le vérifient à chaque exécution.

---

## Le catalogue de collecte comme contrat structurant

Le catalogue n'est pas une page de présentation : c'est un contrat déclaratif
(`apps/api/services/collection_catalog.py`) consommé par trois couches.

```
                 ┌─────────────────────────────┐
                 │  Catalogue (code versionné) │
                 └──────────────┬──────────────┘
      ┌─────────────────────────┼─────────────────────────┐
      ▼                         ▼                         ▼
 Interface web            Conception                Data platform
 cartes, simulateur   types autorisés,          dimension conforme
                      besoin d'enquêteurs        dim_collection_service
```

Conséquence concrète : ajouter un mode de collecte se fait à un seul endroit.
Le simulateur, la validation des questionnaires et la dimension de l'entrepôt
en héritent.

Le point le plus important est l'**intersection** des types de questions. Un
questionnaire multimode doit tenir dans le mode le plus contraint, sinon la
comparabilité entre canaux est perdue. La plateforme refuse donc une photo dans
un dispositif CAPI + CATI, sauf restriction explicite par `only_services` — et
la restriction est alors tracée jusqu'à l'analyse.

---

## Couche opérationnelle

### Le format long des réponses

Une réponse est une ligne : `(interview_id, question_code, repeat_index)` avec
des colonnes typées (`value_text`, `value_number`, `value_date`, `value_json`).

L'alternative — une colonne par question — impose une migration de schéma à
chaque évolution de questionnaire, en pleine collecte. Le format long l'évite
complètement. Le coût, une mise à plat nécessaire pour l'analyse, est payé en
silver, où il est rejouable à volonté.

Le `question_code` est **dupliqué** à côté de la clé étrangère : il survit à la
suppression d'une version et constitue la clé d'analyse stable entre versions.

### L'évaluateur d'expressions

Les règles de pertinence et de contrainte sont écrites par des méthodologues,
stockées en base et exécutées côté serveur à chaque réponse. C'est une surface
d'exécution de code fournie par l'utilisateur.

L'évaluateur parcourt l'arbre syntaxique et n'autorise qu'une liste blanche de
nœuds. Attributs, indexation, lambdas et appels non nommés sont rejetés à la
validation comme à l'exécution. Une variable inconnue vaut `None` plutôt que de
lever : une question non encore posée doit rendre la règle fausse, pas
interrompre l'entretien.

### Idempotence de la synchronisation

Une tablette en zone non couverte accumule des jours de collecte. La remontée
échoue, se reprend, se rejoue. Le `client_uuid` généré par l'appareil rend
l'opération idempotente, et chaque entretien du lot est traité indépendamment :
un enregistrement corrompu ne fait pas perdre la journée.

---

## Data platform

### Pourquoi des contrats de données explicites

`data_platform/contracts.py` décrit chaque table extraite : colonnes retenues,
types cibles, stratégie de chargement, colonnes personnelles.

Sans ce contrat, la data platform lirait le modèle ORM et suivrait ses
évolutions silencieusement — un renommage de colonne casserait un tableau de
bord trois couches plus loin, sans que personne ne sache pourquoi. Le contrat
déplace la rupture à l'extraction, là où elle est immédiatement visible.

### Les trois couches

**Bronze** — copie fidèle, sans transformation métier, en ajout seul. Partitionnée
par date d'ingestion, avec `_ingested_at`, `_batch_id` et `_source_system`.
C'est le journal d'origine : elle seule permet de rejouer intégralement les
couches supérieures, ce qui justifie sa conservation longue.

**Silver** — une ligne par entité, dédupliquée par `ROW_NUMBER()` sur la clé
métier ordonnée par date d'ingestion décroissante. Les durées aberrantes sont
neutralisées, les libellés multilingues résolus, les données personnelles
masquées. C'est la couche que consulte un analyste qui veut comprendre.

**Gold** — schéma en étoile. Dimensions conformes avec membre inconnu pour ne
jamais perdre un fait en jointure, faits porteurs de mesures pré-calculées
(dont les mesures pondérées), et vues de restitution prêtes à l'emploi.

### Local et cloud : le même SQL

Les modèles SQL ne connaissent pas leur moteur d'exécution. En local, DuckDB
déclare des vues sur les fichiers Parquet. Dans Azure, Fabric ou Synapse
déclarent des tables externes sur les mêmes fichiers dans ADLS Gen2.

Ce n'est pas une commodité de développement : cela signifie qu'une
transformation se teste intégralement sur un poste, avec des données réelles
anonymisées, avant d'être promue.

---

## Restitution

### Un seul chemin vers les chiffres

```
             couche gold
                  │
      ┌───────────┴───────────┐
      ▼                       ▼
  Power BI              reports/generate.py
 (DAX sur le            (SQL sur les mêmes
  même modèle)           tables, zéro calcul
                         en Python)
```

Le générateur LaTeX n'a **aucune** dépendance à la base opérationnelle. Il ne
recalcule aucune statistique. S'il en calculait, l'écart avec Power BI ne
serait qu'une question de temps.

### Pondération

Les tables de faits portent `sampling_weight` et des mesures pondérées
pré-calculées. La distinction est faite explicitement partout — colonnes,
mesures DAX, colonnes de tableaux du rapport — parce que confondre effectif
brut et effectif pondéré est l'erreur d'interprétation la plus fréquente sur ce
type de données.

### Effet de mode

En multimode, `mart_mode_effect` compare les résultats par canal et calcule
l'écart maximal. Le rapport insère automatiquement un avertissement au-delà de
dix points. Un tableau de bord qui agrège les modes sans cette vérification
présente comme un fait de population ce qui peut n'être qu'un artefact du canal.

---

## Sécurité et confidentialité

| Niveau | Mécanisme |
|---|---|
| Authentification | PBKDF2-HMAC-SHA256, jetons signés HMAC ; point de bascule vers Entra ID isolé dans `security.py` |
| Autorisation | Rôles applicatifs ; un enquêteur ne voit que ses entretiens, une enquête n'est accessible qu'à son organisation |
| Expressions | Liste blanche AST, testée contre les tentatives d'évasion |
| Requêtes analytiques | Vues en liste blanche et paramètres liés ; aucune requête libre exposée |
| Données personnelles | Marquage `is_pii` à la conception, masquage en silver, contrôle bloquant en gold, test de bout en bout jusqu'au rapport |
| Secrets | Key Vault et identités managées ; aucun secret dans le code ni dans les images |

---

## Ce que la plateforme ne fait pas

Énoncer les limites vaut mieux que les laisser découvrir :

- **L'application terrain hors ligne n'est pas incluse.** Le contrat serveur
  l'est : `/sync/package` et `/sync` définissent précisément ce qu'un client
  doit implémenter, et Power Apps couvre la supervision et le dépannage.
- **La passerelle SMS n'est pas branchée.** Le mode est modélisé de bout en
  bout, mais l'intégration à un agrégateur reste à contractualiser.
- **La pondération de redressement n'est pas calculée.** Les poids de sondage
  sont portés et propagés ; le calage sur marges relève d'un travail
  statistique distinct.
- **Le multilinguisme est structurel mais partiel.** Les libellés sont stockés
  par langue ; l'interface web est en français.
