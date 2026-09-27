SHELL := bash
.PHONY: env install db migrate seed reset dev test lint fmt stop

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
	-@command -v powershell >/dev/null && powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $$_.CommandLine -match 'uvicorn app.main|app.jobs.worker|next dev|next-server|spawn_main' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }" || true
