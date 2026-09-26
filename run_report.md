# BuVi — Running It Locally

## One command

```bash
cd /Users/akhilkrishna/Desktop/BuVi
./scripts/run-local.sh
```

That's it. It brings up Docker infra, waits for every container, applies migrations and seeds the
demo tenant **only if they haven't been already**, starts all 11 backend services and the frontend,
then prints your logins and exits — leaving everything running. Cold start takes ~30s (a few
minutes the very first time, for `npm install` and migrations).

Then open **http://localhost:3000** and sign in:

| Username | Password | Can reach |
|---|---|---|
| `demo-client` | `Demo-Passw0rd!23` | Chat, Dashboards |
| `demo-developer` | `Demo-Passw0rd!23` | + Data Sources, SQL Lab, Semantic, MCP |
| `demo-admin` | `Demo-Passw0rd!23` | + Users, Policies, Audit, Billing, Webhooks |

Other commands:

```bash
./scripts/run-local.sh --status   # what's up, what's down
./scripts/run-local.sh --stop     # stop services + frontend, leave Docker infra running
./scripts/run-local.sh --down     # stop everything, Docker infra included
```

Logs live in `.run-local/logs/` (one file per service, gitignored).

It is safe to re-run `./scripts/run-local.sh` at any time — already-healthy services are left
alone, so it doubles as "start whatever isn't running".

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Sign-in is unavailable right now. The platform services are not reachable.` | api-gateway isn't running | `./scripts/run-local.sh` |
| Script says a tool is missing | Docker / uv / Node not installed | Install what it names; it prints the URL |
| Script says the Docker daemon isn't running | Docker Desktop not started | Start Docker Desktop, wait for "Engine running", re-run |
| `Port 8001 is taken by something that is not a healthy identity-service` | A stale process from a previous run | `./scripts/run-local.sh --stop` then re-run |
| A service failed to become ready | Something in that service | The script prints the last 25 log lines and the full log path |
| Too many sign-in attempts | api-gateway's auth rate limiter | The script already raises the limit for local use; wait ~10s |

**Don't use `scripts/live-flow.sh` to run the app.** It installs `trap _stop_services EXIT`, which
kills every service the moment the calling shell exits — correct for a one-shot test flow that owns
its services, wrong for leaving the app up. That's what `run-local.sh` exists to avoid.

---

## Optional: use your own LLM instead of the offline stub

By default `analytics-orchestrator` uses `ScriptedProvider` — deterministic, offline, canned
answers. Chat works, but no model is called. **This is entirely optional and not part of
`run-local.sh`.**

To point it at a real OpenAI-compatible server (ADR 0022):

```bash
cat > apps/analytics-orchestrator/.env <<'EOF'
ANALYTICS_LLM_PROVIDER=openai_compatible
ANALYTICS_LLM_MODEL=minimax-m2.5
ANALYTICS_LLM_BASE_URL=http://192.168.10.251:3001/v1
ANALYTICS_LLM_API_KEY=your-real-key-here
ANALYTICS_LLM_RESPONSE_FORMAT=json_schema
EOF

./scripts/run-local.sh --stop && ./scripts/run-local.sh   # env files are read once, at startup
```

Three things that bite here:

- **The path matters.** `Settings` uses `env_prefix="ANALYTICS_"` and `env_file=".env"`, resolved
  relative to `apps/analytics-orchestrator/`. A repo-root `.env` or `.env.local` is never read, and
  a variable without the `ANALYTICS_` prefix is ignored.
- **Restart is required.** Env files are read at process start, not hot-reloaded.
- **If your server rejects `json_schema`**, set `ANALYTICS_LLM_RESPONSE_FORMAT=json_object` or
  `none`. The JSON schema is restated in the system prompt regardless, and Pydantic validation is
  the real gate, so `none` usually still works — just with a weaker guarantee.

Already gitignored (`.env*`), so the key can't be committed.

---

## What's actually running

| # | Component | Tech | Port |
|---|---|---|---|
| 1 | Frontend | Next.js 16 / React 19 | 3000 |
| 2 | api-gateway | FastAPI — the only thing the frontend calls | 8000 |
| 3 | identity-service | FastAPI · `identity` schema · OIDC relying party | 8001 |
| 4 | metadata-service | FastAPI · `metadata` schema | 8002 |
| 5 | query-gateway | FastAPI · `query_gateway` schema · SQL validation | 8003 |
| 6 | analytics-orchestrator | FastAPI · `analytics` schema · CrewAI Flow | 8004 |
| 7 | worker-runtime | FastAPI + NATS JetStream consumer | 8005 |
| 8 | visualization-service | FastAPI · ChartSpec validation | 8006 |
| 9 | dashboard-service | FastAPI · `dashboard` schema | 8007 |
| 10 | semantic-service | FastAPI · `semantic` schema | 8008 |
| 11 | mcp-gateway | FastAPI · `mcp` schema | 8009 |
| 12 | notification-service | FastAPI · `notification` schema | 8010 |
| 13 | Postgres | Postgres 16 — platform DB, all 8 schemas | 5432 |
| 14 | Redis | Redis 7 — rate limiting | 6379 |
| 15 | NATS | NATS 2 + JetStream — event bus | 4222 / 8222 |
| 16 | Keycloak | Keycloak 25 — OIDC provider | 8080 |
| 17 | Vault | Vault 1.17 — data-source secrets | 8200 |
| 18 | MinIO | S3-compatible — query results | 9000 / 9001 |
| 19 | MailHog | Fake SMTP + inbox UI | 8025 / 1025 |
| 20 | OTel Collector | Traces | 4317 / 4318 |
| 21 | sample-sales-db | Postgres 16 — a fake *customer* warehouse | 5433 |
| 22 | sample-sales-mysql | MySQL 8.4 — the same, as MySQL | 3307 |

Rows 13–22 are Docker containers (`infra/compose/docker-compose.dev.yml`); rows 1–12 are local
processes started by `run-local.sh`. Request path:

```
Browser -> Next.js :3000 -> api-gateway :8000 -> the other 10 services -> Postgres/Redis/NATS/...
```

The browser only ever talks to Next.js; Next.js only ever talks to api-gateway.

Side UIs while running: **Keycloak** http://localhost:8080 (`admin`/`admin`) · **MailHog**
http://localhost:8025 · **MinIO** http://localhost:9001 · **NATS** http://localhost:8222

---

## Tests

```bash
uv run pytest tests/ -m "not system" -q    # fast, offline, no services needed
make lint && make typecheck                 # ruff + mypy
make backend-e2e                            # full backend regression (manages its own services)

cd web/next-app && npx playwright test      # browser E2E — needs run-local.sh running first
```
