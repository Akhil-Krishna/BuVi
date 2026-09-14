# Standard local workflows for the buvi monorepo (Section 27 of the build spec).
# Targets that depend on infrastructure not yet built announce the phase that
# introduces them rather than failing with a confusing error.

SHELL := /bin/bash
COMPOSE_FILE := infra/compose/docker-compose.dev.yml
WEB := web/next-app

.DEFAULT_GOAL := help
.PHONY: help sync lint fmt typecheck test web-install web-lint web-typecheck web-test \
        web-build check up down migrate seed dev test-login

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
	uv run pytest

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

test-login: ## Phase A1 scripted flow: invite, MailHog, login, logout for all three roles
	scripts/test-login.sh
