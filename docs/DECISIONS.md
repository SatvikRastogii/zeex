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
