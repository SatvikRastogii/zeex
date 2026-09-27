# Decisions

Newest at the bottom. Each entry: decision, reason.

## Stage 0 answers (2026-09-27)

| # | Question | Answer |
|---|---|---|
| 1 | Project name | `z-procure`, UI title "Z-Procure" |
| 2 | GitHub repo | Use the existing `origin` remote (`https://github.com/SatvikRastogii/zeex.git`) |
| 3 | Pause after each stage | No. Run through and report at each stage end |
| 4 | WhatsApp | Simulated in-app vendor inbox. Real Cloud API deferred (optional Stage 14) |
| 5 | Deployment | Local Docker only for now. AWS later (Stage 15, not started) |
| 6 | "Agents" | AI agents only. No human broker role |
| 7 | GST in comparison | Compare including GST (builder setting, default incl.) |
| 8 | Competitor disclosure | The first time a vendor asks, reply only "We have received a lower offer" (when true). If the same vendor asks again on the same thread, the agent may state the exact lower landed price. It never names the other vendor and never reveals target/max/floor |
| 9 | Vendor languages | Hindi and English. Romanized Hindi (Hinglish) is accepted as Hindi input by the parser; outbound messages use the vendor's preferred language |
| 10 | Seed region | Delhi NCR |
| 11 | Vendor personas | Scripted by default, Gemini toggle available |
| 12 | Playwright | Yes: one happy path + three edge-case journeys |
| 13 | Client-specific asks | None; this is an MVP |
| 14 | Local installs | Approved: `uv python install 3.12`, pnpm via corepack |
| 15 | make | Installed by the user (winget `ezwinports.make`) |
| 16 | Graphify | Approved, project-scoped |
| 17 | Fuzzy matching | `pg_trgm` (no pgvector) |

## Stage 1

- **pnpm 10, not 12.** `corepack enable` could not write to `C:\Program Files\nodejs` (no admin), so the shim is in `%APPDATA%\npm`. Corepack 0.34 fetched pnpm 12.6.0, which it could not launch (missing `bin/pnpm.cjs`). Pinned pnpm 10.34.5 (`packageManager` in `frontend/package.json`), which meets "9+".
- **Python 3.12 is uv-managed** (`backend/.python-version`), so system Pythons (3.11/3.13/3.14) are untouched.
- **Sync SQLAlchemy + psycopg 3.** FastAPI runs sync endpoints in a thread pool; the job worker is a plain loop. Simpler to test than async and fast enough for a demo. `pytest-asyncio` is installed per the stack but unused until something needs it.
- **Separate test database** `procure_test`, created by `scripts/db-init/01-test-db.sql` on first container start. Tests never touch demo data.
- **Same-origin API via Next rewrites.** The UI proxies `/api/*` to FastAPI (`frontend/next.config.ts`) so the session cookie can be httpOnly + SameSite=Lax without cross-site cookie rules.
- **Line endings forced to LF** (`.gitattributes`) because the machine has `core.autocrlf=true` and shell hooks break with CRLF.
- **Graphify hook command is `graphify`, not an absolute path.** The installer wrote `C:/Users/.../graphify.EXE` into the shared `.claude/settings.json`; replaced with the bare command so the repo works on other machines. `graphify-out/` is gitignored. Rebuild: `graphify update .`.
- **Graphify install command.** The README says `graphify install --project`; on Windows the default platform is `windows`, so Claude Code needs `graphify install --project --platform claude` (checked in `graphify/install.py`, v0.9.33).
- **Starlette test-client deprecation warning** (suggests `httpx2`). Left as is: harmless, and switching would add an unapproved dependency.
- **Windows note:** `uvicorn --reload` spawns a child that can outlive `make dev` if the parent is killed without Ctrl-C. Ctrl-C in a terminal stops both.
- **Attribution conflict.** The harness default adds an AI co-author trailer; PROMPT.md 2.2.3 forbids it. PROMPT.md wins. The `commit-msg` hook rejects such lines (verified in Stage 1).
- **Stage-end gate commands are not piped through `tail`.** One Stage 2 commit (`809468a`) went in with a mypy error that `| tail` hid; fixed in the next commit (`c6654cf`).

## Stage 2

- **Quantities are integer milli-units of the item's canonical unit** (`qty_canonical_milli`, `qty_milli`). "Integer in smallest unit" had to cover 2.5 tonnes of TMT and 30 bags of cement uniformly; milli-units do both exactly. Unit factors are exact fractions (`numerator`/`denominator`); a conversion that is not a whole number of milli-units is rejected.
- **IST is a fixed +05:30 offset** (`app/jobs/clock.py`), not `zoneinfo`: Windows Python has no tz database, `tzdata` would be a new dependency, and IST has had no DST since 1945.
- **Public-ID sequences do not reset each year.** The year segment is the IST year at creation, and the counter keeps counting (`BOM-2027-00043` can follow `BOM-2026-00042`). Codes stay unique without a per-year sequence.
- **RFQ and delivery codes are derived**, not sequenced: `RFQ-<bom number>-<line>` and `DLV-<po number>-<n>`, each with a unique constraint.
- **`builder_org_id` on every tenant-owned row**, including child rows (lines, quotes, threads, deliveries, ...), so every tenant filter is a single column check.
- **Extra columns beyond the Section 8 minimum**, each needed by a later stage: `catalog_items.code` (stable key), `vendors.item_codes`, `vendors.persona` (demo only), per-dimension reliability in basis points, `sites.area` (coarse location shown to vendors before award) and site contact fields, quote `raw_text`/`stated_total_paise`/`received_at`, thread counters for unclear replies/parse failures/disclosure asks, `messages.rfq_id`/`status`, `recommendations.is_current`, and a single-row `demo_clock` table so API and worker share the demo offset.
- **Audit log is append-only by trigger** on UPDATE/DELETE. TRUNCATE (tests, `make reset`) is not blocked; `make reset` is the only way to wipe it and is for demo data.
- **GSTIN check is format only** (the spec says "validated format"). The checksum is not verified.
- **GST rates in the seed catalog are demo defaults** (cement 18%, bricks/AAC 12%, sand/aggregate 5%, steel/tiles 18%). They are not tax advice; each quote carries its own rate.
- **Seed history:** 30 closed work orders over the last ~6 months relative to the IST date at seed time, generated from a fixed random seed. A re-run skips existing history (keyed by BOM title) and recomputes vendor ratings from it, so two runs give identical results.
- **Phone numbers** use `+91 90000 xxxxx` (admin 0xxxx, builders 1xxxx, vendors 2xxxx) and are fictional. The simulated channel never sends to them.

## Stage 3

- **Auth uses the wall clock, never the demo clock.** OTP expiry, rate limits and the 12-hour session run on real time, so jumping the demo clock forward does not log everyone out. Business rules use the demo clock.
- **Server-side sessions table.** The JWT (HS256, in an httpOnly SameSite=Lax cookie) only carries a session id; each request checks the row for revocation and expiry. Logout revokes the row, so a copied cookie stops working.
- **One login for everyone.** The phone number decides the account: active builder/admin user first, then vendor. Opted-out vendors can still sign in to the inbox.
- **Unknown numbers get the same "sent" response** and no challenge is created. In demo mode, known numbers also get `demo_otp` in the response for the banner, which reveals whether a number is registered. That is acceptable only in demo mode.
- **Only the latest challenge per phone counts.** Requesting a new code makes the older ones unusable. "Latest" is by database insert time; ordering by expiry tied under a frozen clock (caught by a test).
- **Rate-limit window is derived from `expires_at − 5 min`**, so it follows the injected clock in tests.
- **Wrong-kind access is 403** (vendor on a builder endpoint, builder on vendor/admin endpoints). Cross-tenant ids are 404.
- **Owner cannot deactivate or demote themselves**, which prevents locking an org out.
- **Demo accounts endpoint** (`GET /api/auth/demo-accounts`) exists only in demo mode and lists the seeded logins on the sign-in screen.
- **Test cleanup uses DELETE with `session_replication_role = replica`** instead of TRUNCATE (about 4x faster on Docker Desktop); this bypasses the audit trigger for tests only.
- **Permission rows without endpoints yet** (BOM create, shortlist edit, take over, approve, delivery confirm) are covered by the matrix test now; each gets an endpoint test in the stage that adds the endpoint.

## Stage 4

- **Uploads are raw request bodies**, not multipart: `POST /api/boms/validate-file?site_id=…&filename=…` with the file bytes as the body. FastAPI needs `python-multipart` for form uploads, which is not in the approved plan; raw bodies need nothing extra and work the same from `fetch(url, {body: file})`. The same pattern will be used for quote PDFs/photos.
- **Validate, then create.** Validation is stateless (file or JSON rows in, per-row results out). Create re-validates everything and refuses any error, so nothing invalid is ever stored. Create is idempotent on a client-generated `client_ref`.
- **Duplicates are merged automatically** (same item, needed-by date and partial flag), with a warning on both rows, rather than blocking.
- **Earliest feasible needed-by = today + 3 days** (bid window + negotiation + delivery), IST, business clock.
- **One site per BOM.** A `site` column naming another site of the same org is an error asking for a separate BOM; a site of another org reads as unknown (no leak).
- **Whole units.** Quantities of bag/nos/box items must be whole; tonne/cft/kg items may be fractional (to 3 decimals).
- **CSV encoding.** UTF-8 (with or without BOM) and UTF-16 are detected; anything else is decoded as cp1252 (what Excel on Windows writes).
- **XLSX formulas.** Cached values are used. A formula with no saved value (file never opened in Excel) is a row error asking the user to open and save.
- **Publish creates one draft RFQ per line**, taking max rounds and shortlist size from org settings.
- **Revisions.** Editing a line of a published BOM bumps the BOM and RFQ revision and marks the RFQ stale if vendors were already invited. Lines lock once their RFQ reaches approval.
- **Migrations must name constraints.** Autogenerate emitted an unnamed unique constraint whose downgrade could not run; it was renamed to Postgres's default (`boms_client_ref_key`). Check autogenerated migrations for `None` names.
- **mypy ignores openpyxl's missing stubs** (`types-openpyxl` would be a new dev dependency).

## Stage 5

- **Matching runs on publish** for every line, so the builder lands on a ready shortlist. Re-running is allowed until RFQs are sent (`draft`, `matching`, `no_vendors_matched`).
- **Hard filters, in order**: supplies the item/grade (catalog item code, which encodes the grade), linked to this builder, not blocked, opted in and not opted out, valid GSTIN, serves the site (pincode list **or** within radius), capacity left in the IST week of the needed-by date. The first failing filter is the stored reason.
- **Score (0–100)**: distance 30, on-time rate 25, price vs the item's median closed price 20 (no history = half marks), credit days 10 (capped at 30 days), capacity headroom 15 (full marks at 2× the order). Integer maths; weights are code constants, not builder settings (the builder's weights are for quote evaluation).
- **Ties**: score, then distance, then name.
- **Unlinked vendors are invisible.** They never appear in the shortlist, the "not matched" list or the add-vendor list, so a builder cannot discover vendors outside its network.
- **Fewer than 2 matches** keeps the RFQ in `matching` with a warning (1 match) or moves it to `no_vendors_matched` (0), with three suggestions. "Widen radius" re-runs with +10 km; "Allow partial supply" sets the line's partial flag and re-runs. "Allow other brands" is shown as advice only, because BOM lines have no brand constraint yet.
- **Builder edits**: remove (kept as `removed`, restorable) and add (must pass the same filters; max 15).
