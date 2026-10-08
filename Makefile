VENV := .venv/bin
NPM := npm --prefix frontend
API := $(VENV)/uvicorn --factory customer_workflow_agent.api.app:create_app --host 127.0.0.1 --port 8000

.PHONY: install dev dev-api dev-web serve test test-live test-web test-alerts lint format build-web graph \
	prompt-fingerprints metrics metrics-prometheus metrics-grafana fake-llm load reset-data

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

test-alerts:  ## Unit tests for the Prometheus alert rules (needs promtool: brew install prometheus)
	promtool test rules ops/prometheus/alerts_test.yml

lint:  ## Check lint and formatting without changing files
	$(VENV)/ruff check src tests load
	$(VENV)/ruff format --check src tests load
	$(NPM) run lint
	$(NPM) run format:check

format:  ## Apply lint autofixes and format Python and frontend code
	$(VENV)/ruff check --fix src tests load
	$(VENV)/ruff format src tests load
	$(NPM) run format

build-web:
	$(NPM) run build

graph:  ## Re-export docs/workflow-graph.mmd from the compiled graph
	$(VENV)/python -m customer_workflow_agent.graph.export

prompt-fingerprints:  ## Record prompt hashes after bumping a prompt's version (llm/prompts.py)
	UPDATE_PROMPT_FINGERPRINTS=1 $(VENV)/pytest -q tests/llm/test_prompt_versions.py

fake-llm:  ## Fake OpenAI-compatible model server on :8100 (point LLM_BASE_URL at it)
	$(VENV)/uvicorn customer_workflow_agent.devtools.fake_llm:app --port 8100

load:  ## Load test against the fake models (USERS=20 RPM=36 DURATION=2m ...; see load/run.py)
	$(VENV)/python load/run.py

# Local Prometheus (:9090) and Grafana (:3000) for the app on :8000; data goes to var/.
# Grafana is local-only (127.0.0.1) with anonymous admin access and no update checks.
GRAFANA_ENV := CWA_ROOT=$(CURDIR) \
	GF_PATHS_DATA=$(CURDIR)/var/grafana GF_PATHS_LOGS=$(CURDIR)/var/grafana/logs \
	GF_PATHS_PLUGINS=$(CURDIR)/var/grafana/plugins \
	GF_PATHS_PROVISIONING=$(CURDIR)/ops/grafana/provisioning \
	GF_SERVER_HTTP_ADDR=127.0.0.1 GF_SERVER_HTTP_PORT=3000 \
	GF_AUTH_ANONYMOUS_ENABLED=true GF_AUTH_ANONYMOUS_ORG_ROLE=Admin GF_AUTH_DISABLE_LOGIN_FORM=true \
	GF_ANALYTICS_REPORTING_ENABLED=false GF_ANALYTICS_CHECK_FOR_UPDATES=false \
	GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false GF_NEWS_NEWS_FEED_ENABLED=false \
	GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=$(CURDIR)/ops/grafana/dashboards/agent.json

metrics:  ## Prometheus on :9090 + Grafana on :3000 (Ctrl-C stops both); run the app too
	@command -v prometheus >/dev/null && command -v grafana >/dev/null || \
		{ echo "Prometheus/Grafana not found. Install them with:  brew install prometheus grafana"; exit 1; }
	$(MAKE) -j2 metrics-prometheus metrics-grafana

metrics-prometheus:
	mkdir -p var/prometheus
	prometheus --config.file=ops/prometheus/prometheus.yml --storage.tsdb.path=var/prometheus \
		--web.listen-address=127.0.0.1:9090

metrics-grafana:
	mkdir -p var/grafana
	$(GRAFANA_ENV) grafana server --homepath "$$(brew --prefix grafana)/share/grafana"

reset-data:  ## Delete chats and the store's working copy (stop the server first)
	rm -rf var/
