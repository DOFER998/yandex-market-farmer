.DEFAULT_GOAL := help
UV ?= uv
LOCAL := docker compose -f docker-compose.local.yml
PROD := docker compose -f docker-compose.prod.yml
BUILD := $(PROD) -f docker-compose.build.yml
ACCOUNT ?= main
SESSION ?= .secrets/cookies.txt
BROWSER ?= auto
export ACCOUNT SESSION BROWSER

.PHONY: help sync setup-env start lint format test check db-up db-down db-logs migrate migrate-history login auth-check balance daily games account-add account-session docker-run-local docker-run-prod docker-stop-prod docker-logs health
help:
	@echo 'Local: make sync setup-env db-up start; connect via QR in the bot'
	@echo 'Checks: make check | make auth-check balance'
	@echo 'VPS: make docker-deploy (GHCR) | make docker-run-prod (source build)'
	@echo 'Status: make docker-status docker-logs | make docker-stop-prod'
	@echo 'Auth: make login-qr | make login (local browser fallback)'
sync:
	$(UV) sync --locked --extra server --extra browser
setup-env:
	$(UV) run --no-sync python -m scripts.setup_env
start: migrate
	$(UV) run --no-sync python -m src.main
lint:
	$(UV) run --no-sync ruff check src scripts tests migrations
format:
	$(UV) run --no-sync ruff format src scripts tests migrations
test:
	$(UV) run --no-sync pytest -q
check: lint
	$(UV) run --no-sync ruff format --check src scripts tests migrations
	$(UV) run --no-sync pytest -q
db-up docker-run-local:
	$(LOCAL) up -d --wait
db-down:
	$(LOCAL) down
db-logs:
	$(LOCAL) logs -f db
migrate:
	$(UV) run --no-sync alembic upgrade head
migrate-history:
	$(UV) run --no-sync alembic history
login:
	$(UV) run --no-sync python -m scripts.market --session "$$SESSION" login --browser "$$BROWSER"
auth-check balance daily games:
	$(UV) run --no-sync python -m scripts.market --session "$$SESSION" $@
account-add:
	$(UV) run --no-sync python -m scripts.account "$$ACCOUNT" "$$SESSION"
account-session:
	$(UV) run --no-sync python -m scripts.account "$$ACCOUNT" "$$SESSION" --replace
docker-run-prod:
	$(BUILD) build
	$(PROD) stop bot
	$(BUILD) run --rm migrate
	$(BUILD) up -d --no-build --wait
docker-stop-prod:
	$(PROD) down
docker-logs:
	$(PROD) logs -f bot
health:
	$(UV) run --no-sync python -m scripts.health

.PHONY: docker-account-add docker-account-session test-postgres
docker-account-add:
	$(PROD) run --rm --no-deps -v "$$(pwd)/$$SESSION:/tmp/import-session.txt:ro" bot python -m scripts.account "$$ACCOUNT" /tmp/import-session.txt
docker-account-session:
	$(PROD) run --rm --no-deps -v "$$(pwd)/$$SESSION:/tmp/import-session.txt:ro" bot python -m scripts.account "$$ACCOUNT" /tmp/import-session.txt --replace
test-postgres: db-up
	YMF_TEST_POSTGRES=1 $(UV) run --no-sync pytest -q tests/test_postgres.py

.PHONY: emoji-preview
emoji-preview:
	$(UV) run --no-sync python -m scripts.emoji_preview

.PHONY: diagnose-run
export RUN
diagnose-run:
	$(UV) run --no-sync python -m scripts.diagnose_run

.PHONY: diagnose-games
diagnose-games:
	$(UV) run --no-sync python -m scripts.diagnose_games

.PHONY: login-qr
login-qr:
	$(UV) run --no-sync python -m scripts.login_qr

.PHONY: docker-deploy docker-status
docker-deploy:
	$(PROD) pull
	$(PROD) stop bot
	$(PROD) run --rm migrate
	$(PROD) up -d --no-build --wait
docker-status:
	$(PROD) ps

.PHONY: diagnose-daily
diagnose-daily:
	$(UV) run --no-sync python -m scripts.diagnose_daily
