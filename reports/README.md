# Production des rapports

Le générateur lit la couche **gold** de l'entrepôt, produit un fichier `.tex`,
puis le compile en PDF. Rien n'est recalculé en Python : les chiffres du
rapport sont exactement ceux des tables lues par Power BI.

---

## Prérequis

### 1. Le pipeline doit avoir tourné

Le rapport lit l'entrepôt, pas la base opérationnelle. Sans pipeline exécuté,
la génération échoue avec un message explicite.

```bash
make pipeline
```

### 2. Un moteur LaTeX

Seule cette étape « compile ». Le reste du projet est en Python et n'a aucune
étape de compilation.

**Debian / Ubuntu** — installation minimale suffisante pour ce gabarit :

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-science \
    texlive-pictures \
    texlive-lang-french \
    lmodern
```

Correspondance entre les paquets et ce qu'ils apportent au gabarit :

| Paquet | Fournit |
|---|---|
| `texlive-latex-base` | `geometry`, `array`, `babel`, `fancyhdr`, `graphicx`, `hyperref`, `longtable` |
| `texlive-latex-recommended` | `booktabs`, `xcolor`, `caption` |
| `texlive-latex-extra` | `titlesec` |
| `texlive-science` | `siunitx` (nombres au format français) |
| `texlive-pictures` | `pgfplots` (courbe de collecte) |
| `texlive-lang-french` | césure et libellés français |
| `lmodern` | police vectorielle |

`texlive-full` fonctionne aussi mais pèse plusieurs gigaoctets pour rien.

**macOS** : `brew install --cask mactex-no-gui` (ou BasicTeX puis
`sudo tlmgr install titlesec siunitx pgfplots booktabs caption`).

**Windows** : installer [MiKTeX](https://miktex.org/), qui télécharge les
paquets manquants à la première compilation.

**Sans installer LaTeX** : produire le `.tex` seul (voir plus bas) et le
compiler sur [Overleaf](https://overleaf.com) en y déposant `rapport_*.tex` et
`preambule.tex`.

---

## Compiler

```bash
make report SURVEY=ECVM2026
```

Équivalent direct :

```bash
python -m reports.generate --survey-code ECVM2026
```

Sortie dans `var/reports/<CODE_ENQUETE>/` :

```
rapport_ecvm2026.tex     source généré
preambule.tex            charte graphique, copiée à côté du .tex
rapport_ecvm2026.pdf     document final
rapport_ecvm2026.log     journal de compilation
```

### Options

```bash
# Produire le .tex sans compiler (aucun moteur LaTeX requis)
python -m reports.generate --survey-code ECVM2026 --no-pdf

# Choisir le répertoire de sortie
python -m reports.generate --survey-code ECVM2026 --output-dir /chemin/rapports
```

Le moteur se règle par la variable `LATEX_ENGINE` (`pdflatex` par défaut,
`xelatex` ou `tectonic` acceptés).

---

## Compiler à la main

Utile pour déboguer le gabarit ou travailler sur la mise en forme :

```bash
cd var/reports/ECVM2026
pdflatex -interaction=nonstopmode rapport_ecvm2026.tex
pdflatex -interaction=nonstopmode rapport_ecvm2026.tex
```

**Deux passes sont nécessaires** : la première collecte la table des matières
et les numéros de tableaux, la seconde les insère. Une seule passe donne un
document dont le sommaire est vide ou périmé.

`preambule.tex` doit se trouver dans le même répertoire — le générateur l'y
copie automatiquement, mais si vous déplacez le `.tex`, emportez-le avec.

---

## En cas d'échec

Le générateur n'affiche pas le journal LaTeX complet, souvent illisible : il
extrait les lignes d'erreur. Les cas courants :

| Message | Cause | Correction |
|---|---|---|
| `File 'titlesec.sty' not found` | paquet LaTeX manquant | installer `texlive-latex-extra` (voir tableau ci-dessus) |
| `L'enquête … est absente de l'entrepôt` | pipeline non exécuté, ou mauvais code | `make pipeline`, puis vérifier le code avec `SELECT survey_code FROM dim_survey` |
| `PDF : non compilé` | aucun moteur LaTeX trouvé | installer LaTeX, ou rester en `--no-pdf` |
| `Undefined control sequence` | gabarit modifié | lire `rapport_*.log`, chercher la première ligne commençant par `!` |

Pour voir le journal entier :

```bash
less var/reports/ECVM2026/rapport_ecvm2026.log
```

---

## Modifier le rapport

| Fichier | Rôle |
|---|---|
| `reports/latex/rapport_enquete.tex.j2` | structure et contenu du document |
| `reports/latex/preambule.tex` | charte graphique, polices, couleurs, en-têtes |
| `reports/generate.py` | requêtes sur la couche gold et mise en forme des données |

Le gabarit utilise Jinja avec des délimiteurs adaptés à LaTeX, pour éviter tout
conflit avec les accolades :

| Usage | Syntaxe |
|---|---|
| Valeur | `\VAR{ enquete.titre }` |
| Structure | `\BLOCK{ for ligne in tableau }` … `\BLOCK{ endfor }` |
| Commentaire | `%%` (commentaire LaTeX ordinaire, conservé dans le `.tex`) |

Deux règles à respecter en modifiant le gabarit :

1. **Passer tout texte issu des données par le filtre `| tex`.** Un libellé de
   modalité contenant `&` ou `%` casse la compilation sans cet échappement.
2. **Ne pas comparer une chaîne formatée à un nombre.** Les valeurs affichées
   sont formatées à la française (`63,3`) ; les conditions doivent utiliser les
   champs numériques correspondants, par exemple `synthese.taux_realisation`
   et non `synthese.taux_realisation_pct`.

Les tests couvrent ces deux points : `pytest tests/test_pipeline.py -k Rapport`.
