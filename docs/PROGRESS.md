# Progress

## Stage 1: Repository and tooling (done)
- Git hygiene: `.gitignore`, `.gitattributes`, `.env.example`, attribution disabled in `.claude/settings.json`, `commit-msg` hook rejecting AI attribution and emoji (verified by a rejected test commit).
- Postgres 16 in Docker Compose, plus a `procure_test` database.
- Backend: FastAPI skeleton, settings, DB session, `/api/health`.
- Frontend: Next.js 16 App Router shell, black-and-white stylesheet, top bar, `/api` proxy.
- Makefile: `env`, `install`, `db`, `dev`, `test`, `lint`, `fmt`, `stop`.
- Graphify installed project-scoped; first graph built.

## Stage 2: Data model, IDs, money and units (done)
- All Section 8 tables as SQLAlchemy models; one Alembic migration with `pg_trgm`, public-ID sequences and an append-only trigger on `audit_log`.
- `domain/money.py` (paise, half-up rounding, basis points, Indian grouping), `domain/units.py` (exact conversions, milli-units), `jobs/clock.py` (system, demo, fixed clocks; IST helpers), `db/ids.py`, `db/audit.py`.
- Idempotent seed: 3 orgs, 10 users, 6 sites, 12 catalog items, 24 vendors (edge-case vendors included), 30 historical closed orders with price history and derived ratings.
- `make migrate`, `make seed`, `make reset`.

## Next
Stage 3: authentication, roles and tenancy.

## Known gaps
- `make demo` arrives with the demo panel (Stage 12).
- State-machine transition tables arrive with the stages that drive each entity.
