# Raccourcis de développement. Le déploiement réel passe par deploy/.
PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
PIP_DEV := pytest pytest-asyncio httpx ruff

.PHONY: help venv test test-fast lint web web-check e2e shots demo clean

help:
	@echo "make venv       crée $(VENV) (Python $$($(PY) --version)) avec les trois paquets en mode éditable"
	@echo "make test       tous les tests Python (agent, dock, common, scripts de déploiement)"
	@echo "make test-fast  idem sans les tests lents (déploiement, mesure du CPU)"
	@echo "make lint       ruff + svelte-check"
	@echo "make web        compile l'interface dans dock/web/dist"
	@echo "make web-check  vérifie que dock/web/dist correspond aux sources"
	@echo "make e2e        tests Playwright 1024x600 (pile démo)"
	@echo "make shots      comme e2e, et régénère les captures de docs/etapes/a"
	@echo "make demo       agent en mode démo + serveur dock sur http://127.0.0.1:8080"

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -U pip
	$(BIN)/pip install -q -e common -e agent -e dock/server $(PIP_DEV)

test:
	$(BIN)/python -m pytest

test-fast:
	$(BIN)/python -m pytest -m "not slow"

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	cd dock/web && npm run check
	shellcheck -x deploy/lib.sh deploy/pc/*.sh deploy/pi/*.sh scripts/*.sh

web:
	cd dock/web && npm ci && npm run build

web-check:
	./scripts/check-web-dist.sh

e2e:
	cd dock/web && npm run e2e

shots:
	cd dock/web && npm run shots

demo:
	./scripts/dev-stack.sh

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache dock/web/node_modules
