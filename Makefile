# Standard local workflows for the buvi monorepo (Section 27 of the build spec).
# Targets that depend on infrastructure not yet built announce the phase that
# introduces them rather than failing with a confusing error.

SHELL := /bin/bash
COMPOSE_FILE := infra/compose/docker-compose.dev.yml
WEB := web/next-app

.DEFAULT_GOAL := help
.PHONY: help sync lint fmt typecheck test web-install web-lint web-typecheck web-test \
        web-build check up down migrate seed dev dev-gateway dev-metadata dev-query-gateway contracts contracts-check \
        dev-orchestrator dev-worker dev-visualization dev-dashboard dev-semantic test-login \
        test-data-sources test-query-gateway test-analytics-run test-dashboards test-semantics \
        eval-groundedness seed-sample-mysql test-mysql-slice

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Python (uv workspace) ---------------------------------------------------

sync: ## Resolve and install every workspace member
	uv sync --all-packages

lint: ## Ruff lint + format check across the workspace
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Apply Ruff formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy over shared packages and every service's core/domain/application layers
	uv run mypy packages/python
	@for svc in apps/*/; do \
		[ -d "$$svc/src" ] || continue; \
		module=$$(basename "$$svc" | tr '-' '_'); \
		for layer in core domain application; do \
			path="$$svc/src/$$module/$$layer"; \
			[ -d "$$path" ] || continue; \
			echo "mypy $$path"; \
			MYPYPATH="$$svc/src" uv run mypy "$$path" || exit 1; \
		done; \
	done

test: ## pytest across the workspace
	uv run pytest tests packages/python apps

# --- Frontend ----------------------------------------------------------------

web-install: ## Install frontend dependencies from the lockfile
	npm ci --prefix $(WEB)

web-lint: ## ESLint + Prettier check
	npm run lint --prefix $(WEB)
	npm run format:check --prefix $(WEB)

web-typecheck: ## tsc --noEmit
	npm run typecheck --prefix $(WEB)

web-test: ## Vitest unit tests
	npm run test --prefix $(WEB)

web-build: ## Production build (proves the scaffold is healthy)
	npm run build --prefix $(WEB)

check: lint typecheck test web-lint web-typecheck web-test ## Everything CI runs

# --- Local infrastructure (Section 27) ---------------------------------------

up: ## Start Postgres, Redis, NATS, Keycloak, Vault, MinIO, MailHog, OTel collector
	docker compose -f $(COMPOSE_FILE) up -d

down: ## Stop the local infrastructure stack
	docker compose -f $(COMPOSE_FILE) down

migrate: ## Run Alembic upgrade for every service, one schema each
	@test -x scripts/migrate-all.sh \
		|| { echo "scripts/migrate-all.sh missing."; exit 1; }
	scripts/migrate-all.sh

seed: ## Seed a demo tenant, sample-sales-db, and demo users for all three roles
	@test -x scripts/seed.sh \
		|| { echo "scripts/seed.sh missing."; exit 1; }
	scripts/seed.sh

dev: ## Start identity-service with reload on :8001
	cd apps/identity-service && uv run --package identity-service \
		uvicorn identity_service.main:create_app --factory --reload --port 8001

dev-gateway: ## Start api-gateway with reload on :8000
	cd apps/api-gateway && uv run --package api-gateway \
		uvicorn api_gateway.main:create_app --factory --reload --port 8000

dev-metadata: ## Start metadata-service with reload on :8002
	cd apps/metadata-service && uv run --package metadata-service \
		uvicorn metadata_service.main:create_app --factory --reload --port 8002

dev-query-gateway: ## Start query-gateway with reload on :8003
	cd apps/query-gateway && uv run --package query-gateway \
		uvicorn query_gateway.main:create_app --factory --reload --port 8003

dev-orchestrator: ## Start analytics-orchestrator with reload on :8004
	cd apps/analytics-orchestrator && uv run --package analytics-orchestrator \
		uvicorn analytics_orchestrator.main:create_app --factory --reload --port 8004

dev-worker: ## Start worker-runtime (JetStream run consumer) on :8005
	cd apps/worker-runtime && uv run --package worker-runtime \
		uvicorn worker_runtime.main:create_app --factory --port 8005

dev-visualization: ## Start visualization-service with reload on :8006
	cd apps/visualization-service && uv run --package visualization-service \
		uvicorn visualization_service.main:create_app --factory --reload --port 8006

dev-dashboard: ## Start dashboard-service with reload on :8007
	cd apps/dashboard-service && uv run --package dashboard-service \
		uvicorn dashboard_service.main:create_app --factory --reload --port 8007

dev-semantic: ## Start semantic-service with reload on :8008
	cd apps/semantic-service && uv run --package semantic-service \
		uvicorn semantic_service.main:create_app --factory --reload --port 8008

contracts: ## Export OpenAPI documents and platform-contracts JSON Schemas into contracts/
	scripts/gen-openapi.sh
	uv run python scripts/export_json_schemas.py

contracts-check: ## Fail on OpenAPI drift or unversioned breaking changes
	scripts/diff-contracts.sh

test-login: ## Scripted flow through api-gateway: invite, MailHog, login, logout, rate limit
	scripts/test-login.sh

test-data-sources: ## Phase A3 scripted flow through api-gateway: add, test, sync, browse sample-sales-db
	scripts/test-data-sources.sh

test-query-gateway: ## Phase A4 scripted flow: valid SELECT capped + audited, unsafe SQL rejected
	scripts/test-query-gateway.sh

test-analytics-run: ## Phase A5 scripted flow: message -> SSE Section 32, kill/resume executor and worker, budget
	scripts/test-analytics-run.sh

test-dashboards: ## Phase A6 scripted flow: Section 32 Steps A-D over HTTP as a client-role user
	scripts/test-dashboards.sh

test-semantics: ## Phase A7 scripted flow: metric lifecycle over HTTP, used by the chat flow
	scripts/test-semantics.sh

seed-sample-mysql: ## Re-apply the MySQL twin of the sample customer database (Phase A8)
	scripts/seed-sample-sales-mysql.sh

test-mysql-slice: ## Phase A8 scripted flow: Section 32 against MySQL with an approved metric
	scripts/test-mysql-slice.sh

eval-groundedness: ## Section 25 groundedness eval: metric usage and insight grounding per run
	uv run --package analytics-orchestrator pytest -s -o addopts="" \
		apps/analytics-orchestrator/src/analytics_orchestrator/tests/integration/test_groundedness_eval.py
