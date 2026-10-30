# Common MUX workflows. Run `make help` for the list.

PYTHON ?= python3
SERVER := mux/server
WEB := mux/web
VENV := $(SERVER)/.venv

.PHONY: help setup setup-server setup-web demo web server tunnel funnel db-up db-down sandbox-image test test-server test-docker test-release typecheck-server check-web check release-check check-all

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

setup: setup-server setup-web ## Install backend and frontend dependencies

setup-server: ## Create mux/server/.venv, install the backend, copy .env.example
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e '$(SERVER)[dev]'
	@test -f $(SERVER)/.env || cp $(SERVER)/.env.example $(SERVER)/.env

setup-web: ## Install the web app's npm packages
	cd $(WEB) && npm ci

demo: ## Run the web app in demo mode (no backend, no keys) on :3000
	cd $(WEB) && NEXT_PUBLIC_DEMO_MODE=true npm run dev

web: ## Run the web app against the backend on :3000
	cd $(WEB) && npm run dev

server: ## Run the API with reload on :8000
	cd $(SERVER) && .venv/bin/uvicorn mux.main:app --reload --reload-dir mux --port 8000

tunnel: ## Public URLs for the web app and API so teammates on other computers can join (needs cloudflared)
	@./scripts/tunnel.sh

funnel: ## One command: tunnels + API + web app, already wired to the public URLs (needs cloudflared)
	@./scripts/funnel.sh

db-up: ## Start the local Postgres (port 5433) used by the DB tests
	cd $(SERVER) && docker compose up -d --wait

db-down: ## Stop the local Postgres
	cd $(SERVER) && docker compose down

sandbox-image: ## Build the Docker image the coder's builds and tests run in (needs Docker running)
	docker build -t mux-sandbox mux/infra/sandbox-image

test-server: ## Backend scripted tests (no Docker needed — required on every change)
	cd $(SERVER) && .venv/bin/pytest -q -m "not docker"

test-docker: sandbox-image ## Backend tests against real Docker (required before release; Docker + image needed)
	cd $(SERVER) && .venv/bin/pytest -q -m "docker"

test-release: test-server test-docker ## Full release gate: scripted suite + real Docker suite

typecheck-server: ## Backend type check (pyright)
	cd $(SERVER) && .venv/bin/pyright mux tests scripts

check-web: ## Type-check, lint and build the web app
	cd $(WEB) && npx tsc --noEmit && npm run lint -- --max-warnings=0 && NEXT_DIST_DIR=.next-build npm run build

test: test-server ## Alias for test-server (scripted only)

check: test-server typecheck-server check-web ## Everything to run before pushing (scripted only)

release-check: test-release ## Everything required before a release (scripted + real Docker)

check-all: test-release typecheck-server check-web ## Everything at once: scripted tests + real Docker + typecheck + web build
