# Flux Power Automate

Chaque flux est decrit par son declencheur, ses etapes et la condition d'arret.
Les appels HTTP visent l'API operationnelle ; l'authentification passe par un
compte de service dont le jeton est stocke dans Azure Key Vault et lu par le
connecteur Key Vault, jamais en dur dans le flux.

---

## 1. Rafraichissement du pipeline analytique

**Declencheur** : recurrence, toutes les 4 heures entre 6 h et 22 h.

1. `HTTP` — `POST {{API_BASE}}/api/v1/analytics/pipeline/run`
   En-tete `Authorization: Bearer @{body('Obtenir_le_secret')?['value']}`
2. `Analyser le JSON` sur la reponse.
3. `Condition` — `success` vaut-il `true` ?
   - **Oui** → `Actualiser un jeu de donnees Power BI` sur le modele semantique.
   - **Non** → publier dans le canal Teams « Exploitation » le nom de l'etape
     en echec et son message d'erreur, puis arreter le flux.

> Le rafraichissement Power BI n'est jamais declenche si le pipeline a echoue :
> mieux vaut un tableau de bord daté que des chiffres faux.

---

## 2. Alerte qualite

**Declencheur** : a la suite du flux 1, en cas de succes.

1. `HTTP` — `GET {{API_BASE}}/api/v1/surveys/{{SURVEY_ID}}/quality/flagged?threshold=0.6`
2. `Condition` — le nombre d'entretiens signales depasse-t-il 10 % des
   entretiens du jour ?
3. Si oui, `Publier un message Teams` au superviseur, avec les cinq entretiens
   au score le plus faible, leur enqueteur et leurs anomalies.
4. `Creer une tache Planner` de controle, echeance a 48 heures.

---

## 3. Alerte de retard de collecte

**Declencheur** : quotidien, 8 h.

1. `HTTP` — `GET {{API_BASE}}/api/v1/surveys/{{SURVEY_ID}}/progress`
2. Calculer l'avancement theorique : jours ecoules divises par duree planifiee.
3. `Condition` — l'ecart entre realise et theorique depasse-t-il 15 points ?
4. Si oui, courriel au chef de projet avec le detail par mode et par region,
   et proposition d'arbitrage : renforcer le terrain, prolonger la periode ou
   reduire la cible.

---

## 4. Fermeture automatique d'un quota sature

**Declencheur** : a la suite du flux 1.

1. `HTTP` — `GET {{API_BASE}}/api/v1/surveys/{{SURVEY_ID}}/quotas`
2. `Appliquer a chacun` sur les quotas dont `is_full` vaut `true` et
   `is_blocking` vaut `true`.
3. Notifier le superviseur du croisement sature, afin qu'il redeploie les
   enqueteurs vers les croisements encore ouverts.

> La plateforme bloque deja la collecte sur un quota sature ; ce flux sert a
> declencher la reaction humaine, pas le controle technique.

---

## 5. Rapport hebdomadaire

**Declencheur** : lundi, 7 h.

1. `HTTP` — `POST {{API_BASE}}/api/v1/analytics/pipeline/run`
2. `Azure Container Instances` ou `Azure Function` — executer
   `python -m reports.generate --survey-code {{SURVEY_CODE}}`
3. `Obtenir le contenu du fichier` depuis le stockage de sortie.
4. `Envoyer un courriel` au commanditaire, PDF en piece jointe, corps du
   message reprenant le taux de realisation et la date de fin projetee.

---

## Variables d'environnement

A definir dans la solution Power Platform, pas dans chaque flux :

| Variable | Exemple | Usage |
|---|---|---|
| `API_BASE` | `https://collect.exemple.org` | Racine de l'API |
| `SURVEY_ID` | `75410e11e131...` | Enquete suivie |
| `SURVEY_CODE` | `ECVM2026` | Code pour la generation du rapport |
| `KEYVAULT_SECRET` | `jodraff-api-token` | Nom du secret dans Key Vault |
