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

## Stage 6

- **Jobs table + worker** (`app/jobs/queue.py`, `python -m app.jobs.worker`) instead of Step Functions/EventBridge/SQS. Claims use `FOR UPDATE SKIP LOCKED`; each job runs in its own transaction; failures retry after 1, 2, 4, 8 minutes, then the job is marked `failed` (shown on the Demo Control Panel). Jobs stuck `running` for 5 minutes (dead worker) go back to `pending`.
- **Jobs sharing an ordering key run strictly in enqueue order**, even if an earlier one is waiting to retry. An identity column `jobs.seq` gives the enqueue order (`created_at` is the transaction time and ties within one transaction).
- **Handlers are idempotent**: each re-checks the current state (invitation still queued, RFQ still bidding, revision unchanged) and does nothing otherwise. Enqueues use dedupe keys.
- **Demo clock = real time + an offset stored in the database**, shared by the API and the worker. It only moves forward. The admin "advance" and "next event" controls move it and then run every job that became due, in order, in the same request, so the demo is deterministic even without the worker. Tests use the same mechanism.
- **Working hours** default to 09:00-20:00 IST, every day (no weekends or holidays). Invites and notices wait for the next opening; the bid window opens when the first invite can go out. The bid close itself is not deferred.
- **Reminder** at 50% of the bid window, moved into working hours. If waiting for the next opening would reach the close, it goes at the last working minute before half-way instead. One reminder only, and only to vendors still in `invited` (not responded, blocked or skipped).
- **24-hour window (simulated)**: free text is only allowed within 24 h of the vendor's last inbound message; otherwise callers must use a template. Every outbound message records whether it was inside the window.
- **Opt-out**: `STOP`, `unsubscribe` and the Hindi equivalents (whole message only) opt the vendor out immediately and for every builder. The only message sent afterwards is the opt-out confirmation. `START` opts back in. Anything queued for an opted-out vendor is stored as `blocked`, audited, and never shown in the vendor's inbox.
- **Inbound dedupe** on `external_message_id` (`sim-in-<vendor>-<client id>`). Device time is kept as `sent_at` (capped at our clock), so out-of-order arrivals display in the order they were written. The simulated inbox lets the server stamp the time, because the browser's real clock is behind the demo clock.
- **Templates are English and Hindi**, chosen by the vendor's first language. Hinglish replies are accepted as input (Stage 7 onwards).
- **Invites never include the exact site address** (area only); the address goes to the winner with the PO.
- **Stale RFQ re-send**: editing a line after invites went out queues an `rfq_update` message to invited vendors (closes the Stage 4 gap).
- **`make stop` on Windows** runs `scripts/stop-dev.ps1`, which matches dev processes by command line. Killing by port missed uvicorn children that inherited the socket, and a stale API kept serving old code during a manual check.

## Stage 7

- **Gemini is called over its documented REST endpoint** (`models/{model}:generateContent`) with the stdlib, not an SDK, so there is no new dependency. The key goes in the `x-goog-api-key` header (never the URL). Checked against ai.google.dev on 2026-09-27: the generateContent reference documents `generationConfig.response_mime_type`, while the newer Interactions API uses `response_format`. To stay on documented ground the provider only sets `response_mime_type: application/json` and puts the JSON Schema in the prompt; Pydantic validation, one retry and the deterministic fallback cover any drift. **The Gemini path is untested here: no key was provided and tests never call the network.** Model IDs come from `GEMINI_PARSE_MODEL` / `GEMINI_WRITE_MODEL`. If `LLM_PROVIDER=gemini` is set without them, the app logs an error and uses the mock; the top bar shows which provider is live.
- **Every LLM output is a strict Pydantic schema** with `extra="forbid"`: amounts are digit strings, units and intents are enums, text fields are short. Invalid output → one retry → `None`, and the caller uses deterministic code (`domain/quote_text.py`). An extra field such as `"action": "approve"` makes the whole output invalid.
- **Deterministic parser = fallback + mock.** It only accepts a price with money context (currency marker or "per"/"/"/"ka rate"), so "30 bags" or "50 kg bags" are never read as rates. It reads English and Hinglish (GST alag/sahit, "3 din mein supply").
- **Mock vision is a lookup**: the mock can't read images, so the demo scans register their expected reading by SHA-256 in `storage/samples/mock_vision.json`. Unknown images come back unreadable and the vendor is asked to resend. The UI labels the mock as Simulated.
- **Pillow** is used to draw the scanned-style demo image. It is already installed as a dependency of reportlab (approved), so nothing new was added.
- **Parsing runs as a job** (`parse_quote`, deduped per message), so a slow model never blocks a request. Buttons (Yes/Edit/RFQ pick) and "withdraw" are handled inline; they need no LLM.
- **Statuses**: form quotes are confirmed at once (the vendor typed the values), unless the price is a likely typo. Every document or text quote waits for the vendor's Yes. A quote received after the bid close is stored as `rejected` with a `late` flag, and the vendor is told politely.
- **Checks done in code** (never by the LLM): rate list → keep only the RFQ item's row; unit → converted to the canonical unit (₹7,600/t → ₹380/bag) or flagged; qty × rate vs stated total (±₹1, with or without GST and freight); ±25% vs the reference median (closed orders in the region over 90 days, else current confirmed quotes); missing validity → today + 7 days, flagged; validity must outlast the bid close by 2 days; instruction-like text → `suspicious_content` flag (treated as data; changes nothing).
- **Ambiguous documents**: a file sent outside an RFQ conversation by a vendor with more than one open RFQ gets a list picker; the tap is linked back with `reply_to`.
- **File limits**: 10 MB; PDF/PNG/JPG/WEBP by magic bytes (not the name); PDFs with fewer than 20 characters of text are treated as scans.
- **ruff E501 is off**: the formatter owns line width; only long strings and regexes exceeded it.
- **Shell tooling note**: backslashes inside heredocs sent through the Bash tool were collapsed (`\\b` became a backspace byte), which also caused several earlier "unexpected EOF" failures. Code containing backslashes is written with the file tools; a control-character scan found no other damage.
- **`scripts/stop-dev.ps1` matches `app.main:app`**, not `uvicorn app.main` (the process is `uvicorn.exe`). The earlier pattern missed and left a stale API serving old code.

## Stage 8

- **Landed cost** follows the spec formula exactly: unit price (per canonical unit) + GST when comparing including GST and the quote excludes it (the reverse removes GST) + freight ÷ qty + unloading ÷ qty. Freight and unloading are taken as quoted, with no GST added on them. Fractions throughout; one half-up rounding at the end. Qty for spreading freight = the quantity the vendor will actually supply (capped at the line quantity).
- **Scores** (each component 0–1, then weighted, reported per component): price = cheapest landed ÷ this landed; delivery = 1 − days after the earliest qualified delivery ÷ days from that earliest delivery to needed-by; payment = credit days ÷ the best credit offered; reliability = average of the vendor's on-time, quantity-accuracy and response rates; quality = the vendor's quality rate. Weights come from the builder's settings and must sum to 100.
- **Ties**: landed cost, then earlier delivery, then higher on-time rating, then earlier quote.
- **Disqualified** (with the reason shown): delivery after needed-by, no delivery date, expired validity, validity shorter than bid close + 2 days, less than the full quantity when partial supply is not allowed, unit not comparable, RFQ item missing from a rate list. There is no brand/grade constraint on BOM lines yet, so "wrong grade/brand" is covered by the catalog item match (the grade is part of the item).
- **Above the builder's maximum**: shown and flagged, never hidden; excluded from L1 and from split proposals unless an override is passed (the override UI arrives with approval in Stage 10).
- **L1 = best overall score**; the lowest landed price is marked separately (both in the table, with the required note).
- **Split award** only when no qualified vendor can cover the full quantity. Each vendor's supply is capped by their offered quantity and their remaining weekly capacity in the needed-by week (after reservations), in score order. A vendor is skipped if the remaining piece is below their minimum order. Any uncovered quantity is reported as a shortfall with options. Never silently short.
- **Fewer than 2 confirmed quotes at close**: the window is extended once (a full bid window from the next working time), non-responders get another reminder, and the builder sees a note. After the extension: 1 quote → recommendation and straight to `awaiting_approval` (no negotiation, so the agent never implies competition that does not exist); 0 quotes → `insufficient_quotes`.
- **Target and maximum price are private**: set on the comparison screen, stored on the RFQ, shown only to builder users, never to vendors or in any LLM prompt (tested in Stage 7 and again in Stage 9).
- **Clock jumps replay each job at its own scheduled time** (`run_due` pins the clock to `run_at`). Without this, an extension triggered during a 3-day jump counted from the end of the jump, not from the missed close. The live worker still uses the real clock.

## Stage 9

- **Negotiation starts automatically after evaluation** when at least 2 vendors are shortlisted (top 3 qualified, not above max). One thread per vendor; the first message on each thread begins "Automated assistant for {builder}."
- **Prices are compared as landed cost per canonical unit** and restated in each vendor's own terms (their GST basis, freight and unloading) before being written into a message. Asks are rounded up to a whole rupee so they never fall below the floor.
- **Pricing rules** (`domain/pricing.py`): benchmark = best current landed offer among the shortlist (real, never invented); floor = 92% of the reference median (landed price of closed orders for the item in the region, else the median of current offers). Non-benchmark vendors are asked to match the benchmark; the benchmark vendor is asked for 3% (round 1), 2% (round 2), then "best and final" (no number). If the ask would not be below the vendor's current offer (already at the floor), the thread closes with their offer as final.
- **The writer receives only**: vendor name, language, the vendor's last message (marked untrusted), the purpose, the exact price text, quantity and RFQ code. Never target, maximum, floor or other vendors' names (asserted over every captured prompt). Output with any number outside the allowed set is regenerated once, then replaced by a fixed English/Hindi template.
- **Reply rules, in order**: abusive → human; asks for a call → human; changes non-price terms → human with "re-score before accepting" (never auto-accepted); asks for the competing price → disclosure policy; other question → human; unclear → one clarification, second unclear → human; LLM output invalid twice (across replies) → human; reject → their last offer stands; accept ("ok"/"done") → their best-and-final at our last ask, never a deal; price → recorded if lower, then next round, final after round 3 or when the target is reached.
- **Disclosure** (Stage 0 answer 8, org setting `disclosure`): first ask → "We have received a lower offer." (only when one really exists; the benchmark vendor is told theirs is among the best); second and later asks → the exact lower landed price restated in the vendor's terms. Never a vendor name. `off` never discloses; `lower_offer_only` never gives the number.
- **Negotiated prices become new confirmed quote revisions** (source `negotiation`), copying all other terms, so evaluation and approval always work from quotes. A higher "counter" never replaces a lower confirmed offer.
- **Timers**: reply due in 3 working hours (org setting); one nudge, then `timed_out` (last offer stands). Negotiation deadline = the sooner of 24 hours and 2 hours before the end of the earliest shortlisted quote's validity day; at the deadline every open thread closes and the RFQ goes to approval.
- **Debounce**: the first reply schedules one parse 45 seconds later; everything the vendor sends in between is merged into that one parse.
- **Stale replies**: a reply that answers an older round's message (via `reply_to`), or was written before our latest message, is stored and marked stale but never changes state.
- **Take over**: owner or purchase manager. The thread moves to `needs_human`; the agent's send path refuses to send on any thread that is not active, and timeouts/nudges stop. The builder can then message the vendor directly and close the thread.
- **When every thread is finished** (closed, timed out or with a human), the recommendation is re-scored with the negotiated revisions and the RFQ moves to `awaiting_approval`.
- **Scripted personas** (`agents/personas.py`) produce deterministic replies for tests and, in Stage 12, the demo.

## Stage 10

- **Approval is idempotent and optimistic.** The client sends an idempotency key (a repeat returns the first result; nothing new is created) and the RFQ version it was looking at. A stale version, or losing a race to a concurrent approval (SQLAlchemy version check → `StaleDataError`), returns 409 "Already approved by {name} at {HH:MM}".
- **Approval limits**: the approval value is the sum of the work-order totals (incl. GST and freight). A purchase manager whose limit (their own, else the org setting, ₹5,00,000 by default) is below it gets `routed_to_owner`: recorded, shown on the owner's dashboard, RFQ unchanged. Owners approve any amount.
- **Blocked approvals**: an expired offer (409; "Reconfirm" asks the vendor and briefly re-opens bidding so their fresh quote is accepted), an offer above the maximum price without the override, a vendor that cannot cover the quantity alone (approve the split instead).
- **Capacity reservation** takes a row lock on the vendor (`SELECT ... FOR UPDATE`) and on that vendor/item/week's live reservations, so two builders awarding the same vendor for the same week are serialised; the second sees the first's reservation and is refused with a runner-up hint. Nothing is issued when any allocation fails (the whole award rolls back).
- **Work orders**: unit price per canonical unit in the vendor's quoted basis; GST shown separately (backed out when the quote included it); freight and unloading pro-rated for split allocations; PDF generated with reportlab and stored. The winner gets `award_notice` then `po_issued` with the exact address and site contact (first time the address is shared); vendors who quoted and lost get `not_selected` once.
- **Vendor confirmation** within the org's `po_confirm_working_hours` (4). Confirm/Decline buttons on the PO message. Decline or expiry → capacity released, the vendor's quote withdrawn (so a re-bid cannot award them again), RFQ back to `awaiting_approval` with the runner-up (next qualified, not above max, can cover, validity not passed) shown as a one-tap approval. No valid runner-up → "Re-open bidding" re-invites the other vendors with a fresh window.
- **Cancellation**: cancelling a BOM closes its negotiation threads, drops queued invites, cancels issued/confirmed/in-delivery POs (vendor told, capacity released) and sends `rfq_cancelled` to the other invited vendors. A fully awarded BOM cannot be cancelled; its POs are cancelled individually.
- **"Send for review"** is available to every builder role (site engineers included); it records the request for the owner and changes nothing else. "Compare again" re-scores.
- **BOM status** now advances to `partially_awarded` / `awarded` as its RFQs are awarded.
- **Process note**: one commit (`333944e`) went in with mypy errors in a new test file because a `;` in the shell command let the commit run after a failing check; fixed in `2b55f11`. Gate commands are chained with `&&` only.

## Stage 11

- **Dispatch** (vendor, from the inbox's "My work orders" panel): vehicle number and quantity (blank = everything still to come). Only after the vendor confirmed the PO. Partial dispatches only when the PO allows partial supply (split awards always do). Idempotent on a client reference.
- **Receipt** (any builder role, including site engineers): quantity + a photo (image bytes, checked by magic number). "Receipt before dispatch" is refused. Over-delivery is flagged and only the ordered quantity is accepted. Short delivery is flagged; the PO stays in delivery for the rest, or the owner/PM closes it with a shortfall note. A delivery after the needed-by date records `late_days`. The vendor gets a `delivery_update` with what was received.
- **Invoice check**: the builder records the invoice number, the PO number printed on it, unit price and total (file optional). Flags: different PO, unit price differs from the PO, total differs (±₹1) from the PO total pro-rated to the quantity actually received. A flagged invoice is never accepted automatically; an owner/PM accepts it with a written reason.
- **Close** (owner/PM): needs a delivered PO (or a short one with a note) and a matched or accepted invoice. Closing appends to price history (unit price and landed price per unit, region = site pincode prefix) and recomputes the vendor's ratings from all their closed POs: on time (no late delivery), quantity accuracy (exactly the ordered quantity, no short/over flags), invoice match (first invoice matched), response rate (RFQs quoted ÷ RFQs invited). The RFQ closes when all its POs are finished, the BOM when all its RFQs are.
- **Remaining builder screens added here**: Vendors directory (linked vendors only, ratings, block/unblock by owner/PM, audited), Settings (owner edits; others read-only), Audit log (read-only, this org only, filter by entity/action, paged).
- **Demo clock determinism fix**: the admin "advance" endpoint and the background worker can both pick up due jobs. The endpoint now waits for any job the worker is running and drains again before returning, so the clock never "returns early" with half a flow applied (found in the browser check: a vendor reply arrived before the negotiation had started and was, correctly, not treated as a reply).
- **Bug fixed during tests**: receipt double-counted the current delivery (autoflush) and marked a short PO delivered.

## Stage 12

- **Scenarios are played through the real API** (`app/demo/scenarios.py`, in-process test client): builders publish and approve, vendors quote and reply from their inbox, the admin moves the clock. Nothing is written to the database directly, so every rule and message applies. The loader needs `httpx` (already a dev dependency, installed by `uv sync`).
- **Demo points**: 1 awaiting approval with L1 Delhi Cement Depot at ₹380 after 3 rounds; 2 awaiting approval with a complete split; 3 bidding with five documents (two unconfirmed); 4 Sharma's PO to Gupta confirmed, Greenline's approval of Gupta will be refused for capacity; 5 a handoff ("asked for a phone call"); 6 PO expired, runner-up offered; 7 short delivery and a flagged invoice. Checked by `tests/test_demo.py`.
- **Load order** 6, 7, 4, 1, 2, 5, 3: scenarios whose demo point has no live timers load first, and the one left mid-bidding loads last, so later clock jumps don't disturb earlier ones. Scenario scripts act as whichever vendor actually won (scores decide, not the script).
- **Vendor auto-reply** (demo only): after the agent messages a vendor, a `persona_reply` job answers 10 demo-minutes later through the normal inbound path, following the vendor's persona (quote, "Yes", negotiation reply, PO confirmation; slow never answers; vague never quotes). Off while scenarios load (they script replies), on afterwards for live demos. The Gemini toggle lets the LLM rephrase the scripted reply in character, keeping its numbers; with the mock it stays scripted (labelled).
- **Control panel**: load one or all scenarios, full run of scenario 1 to a closed order, reset (with or without loading). These run as a separate process (`python -m app.demo.load`) so the API stays responsive; progress is in `storage/demo_status.json`, polled by the panel. Reset truncates every table (the audit guard allows TRUNCATE) and re-seeds, which signs everyone out.
- **`make demo`** loads all scenarios; `make reset && make demo` is the clean path.
- **Relative `STORAGE_DIR` is anchored at the repo root** (it was resolving against the working directory, so API files landed in `backend/storage`).
- **Approval panel** can approve any other qualified vendor who can cover the full quantity (needed for the capacity-conflict runner-up).

## Stage 13

- **Playwright runs against the dev stack**, not the test database. Global setup runs `python -m app.demo.load --reset --all`, and the config starts the API and UI only if they are not already running. One worker, because the journeys approve orders on shared demo data.
- **Demo test seeds order history** exactly as `make reset` does. Without it every vendor had default ratings, which hid that Shree Balaji outscored Delhi Cement Depot in scenario 1 on real data. Scenario 1 now has Balaji hold at ₹398, and Delhi's ₹380 is L1 in both.
- **Tests force `LLM_PROVIDER=mock`** and clear the Gemini key in `conftest.py`, so no test can reach the network even with a key in `.env`.
- **Graphify (2,088 nodes, 7,360 edges, no import cycles).** Smells worth knowing:
  - `agents/negotiation.py` (≈700 lines) and `api/boms.py` / `api/rfqs.py` (≈530 each) are the largest modules. Split negotiation into pricing glue, reply handling and timers if it grows further.
  - `app.demo.scenarios` imports `app.main` lazily to avoid a real admin → scenarios → main cycle. That is acceptable for demo-only code, but keep it out of product code.
  - The most connected nodes are `Clock`/`DbDemoClock`, `audit()` and `transition()`. That is intended: every rule reads the clock, and every change is audited through one path.

