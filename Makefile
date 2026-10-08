VENV := .venv/bin
NPM := npm --prefix frontend
API := $(VENV)/uvicorn --factory customer_workflow_agent.api.app:create_app --host 127.0.0.1 --port 8000

.PHONY: install dev dev-api dev-web serve test test-live test-web lint build-web graph reset-data

install:  ## Python venv + backend deps, and frontend deps
	python3.13 -m venv .venv
	$(VENV)/python -m pip install -U pip
	$(VENV)/pip install -e '.[dev]'
	$(NPM) install

dev:  ## Backend (port 8000) and frontend (port 5173) together; Ctrl-C stops both
	$(MAKE) -j2 dev-api dev-web

dev-api:
	$(API) --reload --reload-dir src --timeout-graceful-shutdown 2

dev-web:
	$(NPM) run dev

serve: build-web  ## Single process: FastAPI serves the built React app on port 8000
	$(API) --timeout-graceful-shutdown 2

test:  ## Backend tests (no network)
	$(VENV)/pytest

test-live:  ## Live NVIDIA API checks (needs NVIDIA_API_KEY in .env)
	$(VENV)/pytest -m live

test-web:  ## Frontend tests
	$(NPM) test

lint:
	$(VENV)/ruff check src tests
	$(VENV)/ruff format --check src tests
	$(NPM) run lint

build-web:
	$(NPM) run build

graph:  ## Re-export docs/workflow-graph.mmd from the compiled graph
	$(VENV)/python -m customer_workflow_agent.graph.export

reset-data:  ## Delete chats and the store's working copy (stop the server first)
	rm -rf var/
