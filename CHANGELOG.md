# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et la
[gestion sémantique de version](https://semver.org/lang/fr/).

## [0.1.0] — 2026-08-16

Première version de la plateforme. La chaîne complète est fonctionnelle, du
choix du service de collecte jusqu'au rapport PDF.

### Catalogue des services de collecte

- Six services proposés au démarrage : `CAWI`, `CAPI`, `CATI`, `PAPI`, `SMS`,
  `MIXED`, décrits par leurs capacités, coûts, durées et taux de réponse.
- Simulateur de dispositif : taux de réponse combiné des modes séquencés,
  contacts à mobiliser, charge terrain, effectifs d'enquêteurs suggérés.
- Le catalogue est un contrat unique consommé par l'interface, la validation
  des questionnaires et la dimension `dim_collection_service` de l'entrepôt.

### Conception des questionnaires

- Versions immuables une fois publiées, identifiées par une empreinte
  `schema_hash`, ce qui rattache chaque entretien à son instrument exact.
- Logique de questionnaire — pertinence, contraintes, calculs — en expressions
  restreintes évaluées par liste blanche de l'arbre syntaxique.
- Validation à la publication : codes utilisables en SQL, références en avant,
  compatibilité avec les modes retenus, sections répétables, listes de choix.

### Réalisation de l'enquête

- Moteur unique pour tous les modes : navigation par règles, contrôle des
  réponses à la saisie, recalcul des variables calculées, quotas bloquants.
- Score de qualité par entretien : durée rapportée à la référence du mode,
  présence GPS, taux de remplissage, corrections, uniformité des échelles.
- Synchronisation hors ligne idempotente par `client_uuid` ; l'échec d'un
  enregistrement ne fait pas perdre le reste du lot.

### Gestion des enquêteurs

- Habilitations par mode, formation, capacité journalière.
- Affectation automatique par proximité géographique puis équilibrage de
  charge ; transfert tracé ; tableaux de productivité et de qualité.

### Centralisation et traitement

- Contrats de données explicites entre la base opérationnelle et le lakehouse.
- Architecture medallion : bronze append-only incrémental par watermark,
  silver typé et dédupliqué avec masquage des données personnelles, gold en
  schéma en étoile.
- Dix-huit contrôles qualité ; la couche gold n'est pas exportée si un
  contrôle bloquant échoue.
- Le même SQL s'exécute sur DuckDB en local et sur Fabric ou Synapse en Azure.

### Restitution

- Mesures DAX et guide du modèle sémantique Power BI, connecteur Power Apps,
  cinq flux Power Automate.
- Rapports LaTeX lisant exclusivement la couche gold, sans recalcul : les
  chiffres du PDF sont ceux du tableau de bord par construction.

### Infrastructure

- Terraform Azure : Container Apps, PostgreSQL, ADLS Gen2 à espace de noms
  hiérarchique, Azure SQL, Data Factory, Key Vault, identités managées.
- Schéma T-SQL de la couche gold, image conteneur sans privilèges,
  environnement Docker de développement.

### Tests

- 152 tests, dont un test de bout en bout suivant une donnée du terrain
  jusqu'au rapport diffusé et vérifiant qu'aucune donnée personnelle ne fuit.

### Performances

- Chargement bronze ramené de 22 s à 0,6 s sur le jeu de démonstration, en
  passant par un fichier CSV de transit plutôt que des `INSERT` paramétrés.

### Limites connues

- L'application terrain hors ligne n'est pas fournie ; son contrat serveur
  l'est (`/sync/package` et `/sync`).
- La passerelle SMS est modélisée sans être raccordée à un agrégateur.
- Le redressement par calage sur marges n'est pas calculé ; les poids de
  sondage sont portés et propagés.
- L'interface web est en français uniquement, bien que les libellés soient
  stockés par langue.

[0.1.0]: https://github.com/JODRAFF9/jodraff-collect/releases/tag/v0.1.0
