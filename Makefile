# Raccourcis de développement. Le déploiement réel passe par deploy/.
PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
PIP_DEV := pytest pytest-asyncio httpx ruff

.PHONY: help venv test test-fast test-ubuntu lint web web-check e2e shots demo deb clean

help:
	@echo "make venv       crée $(VENV) (Python $$($(PY) --version)) avec les trois paquets en mode éditable"
	@echo "make test       tous les tests Python (agent, dock, common, scripts de déploiement)"
	@echo "make test-fast  idem sans les tests lents (déploiement, mesure du CPU)"
	@echo "make test-ubuntu tests de l'agent avec les versions EXACTES des paquets d'Ubuntu 26.04"
	@echo "make lint       ruff + svelte-check"
	@echo "make web        compile l'interface dans dock/web/dist"
	@echo "make web-check  vérifie que dock/web/dist correspond aux sources"
	@echo "make e2e        tests Playwright 1024x600 (pile démo)"
	@echo "make shots      comme e2e, et régénère les captures de l'étape en cours (docs/etapes/e)"
	@echo "make demo       agent en mode démo + serveur dock sur http://127.0.0.1:8080"
	@echo "make deb        construit le paquet de l'agent : build/mydock-agent_<version>_all.deb"

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -U pip
	$(BIN)/pip install -q -e common -e agent -e dock/server $(PIP_DEV)

test:
	$(BIN)/python -m pytest

test-fast:
	$(BIN)/python -m pytest -m "not slow"

test-ubuntu:
	./scripts/ubuntu-venv.sh
	.venv-ubuntu/bin/python -m pytest common agent dock/server scripts/tests/test_deb.py

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

deb:
	$(PY) packaging/build_deb.py

clean:
	rm -rf $(VENV) .venv-ubuntu .pytest_cache .ruff_cache build dock/web/node_modules
