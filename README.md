# Z-Procure

Demo of an AI procurement agent for construction: BOM upload, vendor matching, WhatsApp-style RFQs (simulated), quote parsing, negotiation, approval, work orders and delivery tracking.

All companies, people, phone numbers and GSTINs in the demo data are fictional.

## Run it

Needs Docker, Node 20+, pnpm 10 (via corepack), Python 3.12 (via uv) and make.

```
make install   # creates .env from .env.example, installs backend + frontend deps
make demo      # Postgres, migrations, seed, and all seven demo scenarios
make dev       # API on :8000, job worker, UI on http://localhost:3000
```

Sign in with a demo phone number from the login page; the OTP is shown on screen. The 10-minute walk-through is in `docs/DEMO_SCRIPT.md`.

Other targets: `make reset` (wipe and re-seed), `make test` (backend tests, no network), `make e2e` (Playwright journeys; resets the dev database), `make lint`, `make stop`.

Works without a Gemini key: the AI is then a clearly labelled mock. To use Gemini, set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` and the model names in `.env`.

## Docs

- `docs/ARCHITECTURE.md`: components, demo vs production mapping, how to switch to real WhatsApp, S3, Step Functions.
- `docs/DECISIONS.md`: decisions made per stage.
- `docs/EDGE_CASES.md`: every edge case, its behaviour and the test that proves it; gaps with reasons.
- `docs/DEMO_SCRIPT.md`: the client demo.
- `docs/PROGRESS.md`: stage status.
