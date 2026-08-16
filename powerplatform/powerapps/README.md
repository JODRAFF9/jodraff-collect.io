# Application Power Apps

Application canevas de supervision et de collecte de secours, connectee a
l'API operationnelle par un connecteur personnalise.

## Connecteur personnalise

Le connecteur est genere depuis la specification OpenAPI publiee par l'API :

```bash
curl https://collect.exemple.org/openapi.json -o jodraff-openapi.json
```

Dans Power Apps : **Donnees → Connecteurs personnalises → Nouveau → Importer
un fichier OpenAPI**. Authentification : OAuth 2.0 sur Entra ID, ou cle API
portee par l'en-tete `Authorization` selon le mode retenu a l'installation.

Restreindre le connecteur aux operations reellement utilisees, pour eviter
d'exposer l'ensemble de l'API a l'application :

| Operation | Chemin | Usage dans l'application |
|---|---|---|
| `listCollectionServices` | `GET /api/v1/collection-services` | Ecran d'accueil : choix du mode |
| `myWorkload` | `GET /api/v1/enumerators/me/workload` | Liste des affectations du jour |
| `syncPackage` | `GET /api/v1/surveys/{id}/sync/package` | Chargement du questionnaire |
| `startInterview` | `POST /api/v1/surveys/{id}/interviews` | Ouverture d'un entretien |
| `saveAnswersBatch` | `POST /api/v1/interviews/{id}/answers/batch` | Enregistrement par page |
| `completeInterview` | `POST /api/v1/interviews/{id}/complete` | Cloture |
| `flaggedInterviews` | `GET /api/v1/surveys/{id}/quality/flagged` | File de controle qualite |
| `validateInterview` | `POST /api/v1/interviews/{id}/validate` | Decision du superviseur |

## Ecrans

1. **Accueil** — services de collecte disponibles et enquete en cours.
2. **Mes affectations** — galerie triee par priorite puis par echeance, avec
   indicateur de retard.
3. **Entretien** — formulaire genere dynamiquement a partir du schema renvoye
   par `syncPackage`. Les regles de pertinence sont evaluees cote serveur :
   l'application demande la question suivante plutot que de reimplementer la
   logique de saut, ce qui evite toute divergence entre les canaux.
4. **Controle qualite** — entretiens signales, paradonnees, boutons valider et
   rejeter avec motif obligatoire en cas de rejet.

## Limites assumees

Power Apps ne remplace pas l'application terrain CAPI :

- le mode hors ligne y est limite aux collections mises en cache, insuffisant
  pour plusieurs jours de collecte sans reseau ;
- la capture GPS continue et l'audit audio ne sont pas disponibles ;
- la synchronisation par lots idempotents n'est pas reproductible simplement.

L'application couvre donc la supervision et le depannage, pas la collecte de
masse en zone non couverte.
