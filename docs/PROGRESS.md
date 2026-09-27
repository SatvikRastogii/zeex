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

## Stage 4: Catalog, BOM upload and validation (done)
- State machines for BOM, RFQ, quote, negotiation and work order as explicit tables; every transition audited.
- CSV/XLSX reading, header synonyms, per-row validation (item alias/trigram match, units, dates, site, partial), duplicate merge.
- Endpoints: catalog, templates (CSV/XLSX), validate (file or rows), create (idempotent), list, detail, original file, publish (RFQs per line), line revision, cancel.
- UI: dashboard (open BOMs + status counts), New BOM (upload or type rows, inline errors, item chooser), BOM detail (publish, cancel, edit line).
- `docs/EDGE_CASES.md` started (Stages 2–4).

## Stage 5: Vendor matching (done)
- Pure matcher (`agents/matching.py`): hard filters with reasons, 0–100 score with a one-line reason, deterministic ties, warnings and suggestions.
- DB runner: capacity reservations per IST week, price position from closed orders, stores proposed invitations and a match report.
- Endpoints: RFQ view, re-match (wider radius / allow partial), candidates, shortlist add/remove (owner and PM only; locked once RFQs are sent).
- UI: Matching review screen, linked from the BOM detail.

## Next
Stage 6: messaging, outreach, jobs and demo clock.

## Known gaps
- `make demo` arrives with the demo panel (Stage 12).
- Stale RFQs are not yet re-sent to vendors (needs outreach, Stage 6).
