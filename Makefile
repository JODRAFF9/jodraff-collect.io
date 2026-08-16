.DEFAULT_GOAL := help
PYTHON ?= python
SURVEY ?= ECVM2026

.PHONY: help install demo serve pipeline bronze transform quality report test lint format clean reset

help: ## Afficher cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Installer les dependances (execution et developpement)
	$(PYTHON) -m pip install -e ".[dev]"

demo: ## Recreer le jeu de demonstration puis executer le pipeline
	$(PYTHON) -m scripts.seed_demo --reset
	$(PYTHON) -m data_platform.runner run

serve: ## Lancer l'API et l'interface web sur http://localhost:8000
	$(PYTHON) -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

pipeline: ## Executer la chaine complete bronze -> silver -> gold
	$(PYTHON) -m data_platform.runner run

bronze: ## Extraire la base operationnelle vers la couche bronze
	$(PYTHON) -m data_platform.runner bronze

transform: ## Rejouer les couches silver et gold sans reextraire
	$(PYTHON) -m data_platform.runner transform

quality: ## Executer les controles qualite de l'entrepot
	$(PYTHON) -m data_platform.runner quality

report: ## Produire le rapport LaTeX (make report SURVEY=ECVM2026)
	$(PYTHON) -m reports.generate --survey-code $(SURVEY)

test: ## Executer la suite de tests
	$(PYTHON) -m pytest -q

lint: ## Controler le style
	$(PYTHON) -m ruff check .

format: ## Corriger le style automatiquement
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

clean: ## Supprimer les artefacts d'execution
	rm -rf var/lake var/warehouse.duckdb var/pipeline_runs var/reports
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

reset: clean ## Tout remettre a zero, base comprise
	rm -f var/operational.db var/operational.db-wal var/operational.db-shm
