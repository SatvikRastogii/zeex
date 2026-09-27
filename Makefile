SHELL := bash
.PHONY: env install db migrate seed reset demo dev test lint fmt stop

env:
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

install: env
	cd backend && uv sync
	cd frontend && pnpm install

db:
	docker compose up -d --wait db

migrate: db
	cd backend && uv run alembic upgrade head

seed: migrate
	cd backend && uv run python -m app.seed.run

# Wipes the dev database schema and reseeds. Demo data only.
reset: db
	cd backend && uv run alembic downgrade base && uv run alembic upgrade head
	cd backend && uv run python -m app.seed.run

# Plays all seven demo scenarios to their demo points (see docs/DEMO_SCRIPT.md).
# Use after `make reset` for a clean run; running it again adds another set.
demo: seed
	cd backend && uv run python -m app.demo.load --all

# Starts Postgres, the API on :8000, the job worker and the UI on :3000. Ctrl-C stops all.
dev: install migrate
	@trap 'kill 0' EXIT; \
	(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd backend && uv run python -m app.jobs.worker) & \
	(cd frontend && pnpm dev) & \
	wait

test: db
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests
	cd frontend && pnpm lint && pnpm exec tsc --noEmit

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

# Stops Postgres and any dev server still holding :3000/:8000 (Windows can orphan
# the uvicorn reloader child).
stop:
	docker compose stop
	-@command -v powershell >/dev/null && powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-dev.ps1 || true
