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

## Stage 6: Messaging, outreach, jobs and demo clock (done)
- Durable job queue + worker (SKIP LOCKED, FIFO per ordering key, retries with backoff, stale-lock recovery), DB-backed demo clock.
- `MessageChannel` + `SimulatedWhatsAppChannel`, English/Hindi template registry (the 10 required templates plus opt-out/opt-in confirmations), 24 h window tracking, opt-out blocking.
- Inbound: dedupe, out-of-order device time, STOP/START.
- Outreach agent: send RFQs (max 15, opted-out skipped), working-hours deferral, 50% reminder to non-responders, bid close → evaluating, closed notices, stale-RFQ updates.
- UI: Send RFQs + invitation statuses on the RFQ page, Vendor Inbox (conversations, template buttons, reply, STOP), Demo Control Panel (clock +15 min / +1 h / next event, pending and failed jobs, event log). `make dev` also starts the worker.

## Stage 7: Quotation intake and parsing (done)
- LLM layer: `LLMProvider`, strict schemas, `structured()` (validate → one retry → None), `MockProvider` (scriptable, records prompts), `GeminiProvider` (REST, untested without a key).
- Deterministic English/Hinglish text parser as fallback.
- Quotation Parser: PDF text layer / scan / photo, rate lists, unit conversion, arithmetic, outlier, validity, suspicious-content and late checks; revisions, confirmation (Yes/Edit), withdrawal, RFQ picker for ambiguous documents.
- Vendor endpoints: quote form, file upload (raw body), demo sample documents. Builder endpoints: quotes per RFQ, original file.
- Six generated sample documents (clean, arithmetic error, rate list, photo, scanned PDF, hidden instructions).
- UI: working quote form, attachments and sample sender in the Vendor Inbox; "Quotes received" table with flags and the original document shown next to the parsed values.

## Stage 8: Evaluation, shortlist and big orders (done)
- Pure rules (`domain/evaluation.py`): landed cost, disqualification, weighted scoring with tie-breaks, L1 vs lowest price, max-price flag, split award with capacity and minimum orders, shortfall.
- Evaluation agent: runs at bid close, stores a Recommendation, shortlists the top 3; extends thin windows once; single quote goes to the builder without negotiation.
- Endpoints: comparison, score again, private target/max limits.
- UI: Quotes comparison (ranked table with score parts and marks, split/shortfall box, private limits).
- Fixed: clock-jump catch-up now runs each job at its scheduled time.

## Stage 9: Negotiation agent (done)
- Pricing engine (benchmark, floor, match/3%/2%/best-and-final, restated in each vendor's terms).
- Agent: threads, LLM writer with number validator and template fallback, reply reader with deterministic fallback, handoff rules, disclosure policy, timeouts with nudge, deadline, debounce, stale replies, negotiated quote revisions, completion → approval.
- Scripted personas; endpoints for thread view, take over, manual message, close.
- UI: Negotiation view (threads side by side with transcripts, offers, handoff reasons, take over).

## Stage 10: Approval, work orders and conflicts (done)
- Approval: idempotent, optimistic locking, limits with routing to the owner, expired-offer block + reconfirm, max-price override, split approval.
- Work Order Agent: PO amounts and PDF, capacity reservation under row locks, winner/loser messages, confirm/decline buttons, confirmation timeout, runner-up, re-bid.
- Cancellation of BOMs and POs with notifications and capacity release; BOM status advances with awards.
- UI: recommendation and approval panel (savings, lowest price, runner-up, override), work orders list and detail, builder navigation, dashboard "Needs action".

## Stage 11: Delivery, invoices, ratings and closure (done)
- Dispatch (vendor), receipt with photo (site), invoice check with flags and human acceptance, close with shortfall notes; price history and vendor ratings; RFQ/BOM closure.
- Remaining builder screens: Vendors directory (block/unblock), Settings (owner), Audit log.
- UI: fulfilment on the work order page; "My work orders" in the Vendor Inbox.
- Demo clock advance now waits for the worker to settle (deterministic demos).

## Stage 12: Demo experience (done)
- Seven scenarios played through the real API to their demo points; full run of scenario 1 to a closed order; reset.
- Persona auto-replies (scripted or Gemini-voiced), per-vendor persona assignment, auto-reply toggle.
- Demo Control Panel: scenarios, reset, full run, personas, clock, jobs, event log.
- `make demo`; `docs/DEMO_SCRIPT.md` (10-minute click-by-click with talking points).

## Stage 13: Hardening and documentation (done)
- Playwright journeys (`frontend/e2e/`, `make e2e`): happy path, big order split, capacity conflict, PDF with hidden text.
- Fixed: scenario 1's L1 depended on vendor history the demo test did not seed; the test now seeds it.
- Tests force the mock LLM provider, so a Gemini key in `.env` cannot make a test call the network.
- `docs/EDGE_CASES.md` complete (every cited test ID checked to exist; gaps listed), `docs/ARCHITECTURE.md`, README setup in three commands.

## Next
Optional Stage 14 (real WhatsApp) and Stage 15 (AWS) were not chosen in Stage 0.

## Known gaps
- Live Gemini is not exercised (no key); the mock provider is labelled Simulated in the UI.
