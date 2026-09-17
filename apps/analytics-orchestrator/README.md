# analytics-orchestrator

Conversations, analytics runs and the CrewAI `AnalyticsFlow` (build spec Sections 3, 8.4, 10, 11,
16, 23; Phase A5). Turns a chat message into a run: intent → schema context → query plan → SQL →
validation and delegated execution through query-gateway → ChartSpec → artifact, streaming
user-safe stage events.

| | |
|---|---|
| Owner | platform / analytics |
| Schema | `analytics` (Section 8.4: `conversations`, `messages`, `runs`, `run_events` append-only; RLS) |
| Port | 8004 |
| Public API (via api-gateway, `chat:use`) | `POST /api/v1/conversations` · `POST /api/v1/conversations/{id}/messages` (`Idempotency-Key`) · `POST /api/v1/runs/{id}/cancel` |
| Internal API | `POST /internal/v1/runs/{id}/execute` (worker-runtime, `analytics-orchestrator:execute`) · `GET /internal/v1/runs/{id}/events` (api-gateway SSE replay, `analytics-orchestrator:events`) |
| Contract | `contracts/openapi/analytics-orchestrator.json`; events `contracts/events/*.v1.json`; `contracts/json-schema/{AnalyticsRunEvent,ChartSpec}.json` |
| Health | `/health/live`; `/health/ready` (Postgres, Redis, NATS required) |
| Dependencies | Postgres, Redis (event fan-out, token ledger), NATS JetStream (`ANALYTICS`, `BILLING`), identity-service, metadata-service (agent context), query-gateway (validate + execute), model provider |
| Decisions | [ADR 0006](../../docs/adr/0006-phase-a5-analytics-orchestrator.md) |
| Runbook | [`docs/runbooks/analytics-orchestrator.md`](../../docs/runbooks/analytics-orchestrator.md) |

## Layout

- `infrastructure/flow/analytics_flow.py` — the CrewAI Flow (`@start`/`@listen`, Section 10 order).
- `application/services/run_executor.py` — per-step persistence, resume, events, timeouts, failure.
- `application/services/model_router.py` — budgets, token ledger, fallback, bounded repair (Section 23).
- `application/services/prompts.py` — system prompts; user data only as tagged JSON blocks.
- `infrastructure/llm/` — `anthropic` provider and the offline `scripted` provider (dev/test only).

## Configuration (`ANALYTICS_` prefix)

| Variable | Default |
|---|---|
| `LLM_PROVIDER` | `scripted` (must be `anthropic` in staging/prod; needs `ANTHROPIC_API_KEY`) |
| `LLM_MODEL` / `LLM_FALLBACK_MODEL` | `claude-opus-5` / `claude-opus-4-8` |
| `RUN_TOKEN_BUDGET` / `TENANT_DAILY_TOKEN_BUDGET` | `60000` / `2000000` |
| `STAGE_TIMEOUT_SECONDS` / `RUN_TIMEOUT_SECONDS` / `MAX_REPAIR_ATTEMPTS` | `90` / `300` / `2` |
| `SCRIPTED_LATENCY_SECONDS` | `0` (dev only, lets the live flow interrupt a run) |

## Run and test

```bash
make dev-orchestrator dev-worker          # :8004 and :8005 (needs make up + migrate)
uv run --package analytics-orchestrator pytest apps/analytics-orchestrator/src/analytics_orchestrator/tests
make test-analytics-run                   # Phase A5 DoD against the real stack
```
