# Z-Procure

Demo of an AI procurement agent for construction: BOM upload, vendor matching, WhatsApp-style RFQs (simulated), quote parsing, negotiation, approval, work orders and delivery tracking.

All companies, people, phone numbers and GSTINs in the demo data are fictional.

## Run it

Needs Docker, Node 20+, pnpm 9+, Python 3.12 (via uv) and make.

```
make install   # creates .env from .env.example, installs backend + frontend deps
make dev       # Postgres, API on :8000, UI on http://localhost:3000
make test      # backend tests
```

See `docs/` for architecture, decisions and progress.
