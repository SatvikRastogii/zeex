# Architecture

## Shape

```
Browser (Next.js, :3000) ──/api proxy──▶ FastAPI (:8000) ──▶ Postgres 16
                                            │                  ▲
                                            ▼                  │
                                       jobs table ◀── worker (python -m app.jobs.worker)
                                            │
             agents: matching · outreach · quote parser · evaluation · negotiation · work orders · delivery
                                            │
                   MessageChannel (simulated WhatsApp)   LLMProvider (mock / Gemini)   FileStore (local)
```

- **One Postgres** holds business data, the job queue, the demo clock and the append-only audit log.
- **The API** is request/response only. Anything that waits (bid close, reminders, nudges, confirmation timeouts, debounced replies) is a row in `jobs` with a `run_at`, which the worker runs.
- **Agents are plain modules** in `backend/app/agents/`. Numbers and decisions come from code in `backend/app/domain/` (pricing, evaluation, units, money). The LLM only reads documents and replies, and phrases messages. Its output is validated against strict schemas, and every number it writes is checked against an allowed set.
- **Three seams** separate the demo from production: `MessageChannel`, `LLMProvider` and `FileStore`, plus the `Clock` behind every business rule.

## Demo vs production

| Real architecture | Demo implementation | Why | Production on AWS |
|---|---|---|---|
| WhatsApp Cloud API | `SimulatedWhatsAppChannel` + Vendor Inbox UI, behind `MessageChannel` (`app/channels/`) | No Meta setup needed for a demo | `WhatsAppCloudChannel`; webhook on API Gateway + Lambda or on the API behind an ALB |
| Step Functions + EventBridge Scheduler | Durable `jobs` table + worker loop (`FOR UPDATE SKIP LOCKED`, retries with backoff, stale-lock recovery) | Same semantics (durable waits, timeouts, retries) with no AWS dependency | One Step Functions execution per RFQ (bid window → evaluate → negotiate → approve → confirm); EventBridge Scheduler for one-off timers |
| Wall-clock time | `Clock` abstraction + Demo Clock (`demo_clock` row, admin "advance") | Bid windows and timeouts shown in minutes, not hours | `SystemClock` only; the demo endpoints are off when `DEMO_MODE=false` |
| SQS FIFO | Same `jobs` table, ordered per `ordering_key` (FIFO via `seq`), inbound dedupe on message ID | Ordering + dedupe without extra infra | SQS FIFO with `MessageGroupId` = thread ID and `MessageDeduplicationId` = WhatsApp message ID |
| S3 | Local `storage/` folder behind `FileStore` (`app/files.py`) | Swappable for S3 later | `S3FileStore` (private bucket, SSE, pre-signed URLs for the builder UI) |
| Gemini | `GeminiProvider` behind `LLMProvider`, plus `MockProvider` (`app/llm/`) | Tests and demo run without a key | Same provider; key from Secrets Manager |
| SES email | Not built; "Email quote" is a simulated upload | Out of demo scope | SES inbound rule → S3 → the same quote intake path as a PDF upload |
| Postgres in Docker | `docker-compose.yml` | Local only (Stage 0) | RDS for PostgreSQL with `pg_trgm` enabled |

## How to switch

### Real WhatsApp (Stage 14)
1. Implement `WhatsAppCloudChannel.send()` with the same contract as the simulated one: store the `Message`, refuse free text outside the 24-hour window (`OutsideWindow`), and never send to an opted-out vendor. The template names in `app/channels/templates.py` become the Meta templates to submit (utility category; the OTP one is authentication).
2. Add a webhook route that verifies the signature header with the app secret and answers the verify-token handshake. Pass each inbound message to `app/channels/inbound.py`, which already dedupes on the message ID, handles STOP/START and routes the message to the agents. Download media immediately (the URLs expire) into the `FileStore`.
3. Make status updates (sent, delivered, read, failed) move forward only.
4. Return the new channel from `get_channel()` when a setting says so. Agents only call `get_channel().send(...)`, so nothing else changes.

### S3
Add an `S3FileStore` with the two methods `save(prefix, filename, data) -> ref` and `read(ref) -> bytes`, and return it from `get_file_store()`. Refs are already opaque keys (`prefix/uuid.ext`), so stored rows stay valid if the objects are copied under the same keys.

### Step Functions / EventBridge / SQS
- **Workflow.** Each job kind registered with `@handler` in `app/jobs/queue.py` is an idempotent unit of work that takes `(db, clock, payload)`. In production each becomes a Step Functions task (or a Lambda it calls). `enqueue(..., run_at=...)` becomes an EventBridge Scheduler one-off schedule or a Step Functions `Wait` state.
- **Ordering.** Per-thread ordering maps to an SQS FIFO message group.
- **Safety.** Handlers already re-check state before acting (for example a stale nudge does nothing if the vendor replied), so at-least-once delivery is safe.
- **Rollout.** The simplest path is to keep the jobs table and run the worker as an ECS service. Move to Step Functions only when you need its visual history.

### Gemini
Set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` and the two model names in `.env`. The top bar then shows the live provider instead of "Simulated (mock)". With a missing key the app logs an error and falls back to the labelled mock. Tests always force the mock.

## Invariants worth keeping in production

- **Money** is integer paise and GST is basis points, rounded half up once at the end. Quantities are milli-units of the item's canonical unit.
- **Tenancy.** Every tenant-owned read goes through `get_owned()`; another tenant's ID returns 404.
- **Audit.** Every state change goes through `domain/states.py` and writes `audit_log`, which a database trigger makes append-only.
- **Confidentiality.** The LLM never receives the target price, maximum price, floor, or competitors' names. A test inspects the recorded prompts.
