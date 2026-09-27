SHELL := bash
.PHONY: env install db dev test lint fmt stop

env:
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

install: env
	cd backend && uv sync
	cd frontend && pnpm install

db:
	docker compose up -d --wait db

# Starts Postgres, the API on :8000 and the UI on :3000. Ctrl-C stops both.
dev: install db
	@trap 'kill 0' EXIT; \
	(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd frontend && pnpm dev) & \
	wait

test: db
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests
	cd frontend && pnpm lint && pnpm exec tsc --noEmit

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

stop:
	docker compose stop
