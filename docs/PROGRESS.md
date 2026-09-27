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

## Stage 3: Authentication, roles and tenancy (done)
- Phone OTP (argon2-hashed, 5 min, single use, 5 attempts, 5 per 15 min) → JWT cookie backed by a revocable 12 h session row.
- Permission matrix (Section 11), role guards, `get_owned` tenant helper (404 across tenants).
- Endpoints: auth (request/verify/logout/me/demo-accounts), org settings (weights sum to 100), users (owner only), sites, vendor messages (own only), admin clock.
- UI: sign-in with the demo OTP banner and demo accounts table, top bar with user · role · org · demo clock, logout, guarded builder/vendor/admin pages.

## Next
Stage 4: catalog, BOM upload and validation.

## Known gaps
- `make demo` arrives with the demo panel (Stage 12).
- State-machine transition tables arrive with the stages that drive each entity.
