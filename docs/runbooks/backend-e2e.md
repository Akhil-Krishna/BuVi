# Runbook: backend e2e suite (Phase A12, the Track A exit gate)

**Owner:** platform · **Runs:** the CI job `backend e2e (Track A exit gate)` on every push and PR; locally with `make backend-e2e`.

## What it runs

`scripts/backend-e2e.sh` runs every step, even after a failure, and then prints one summary.

1. **contracts:** `pytest tests/system -m "not system"`.
   - every `contracts/openapi/*.json` is a valid OpenAPI document;
   - the Section 24 registry (`tests/system/section_24.py`) covers every checklist bullet, and every test it names exists;
   - the static controls hold: no cross-service schema access, no dynamic code execution, non-root images with health checks, dependency audits in CI, and no cloud keys in CI.
2. **Every live DoD flow,** A1 to A12, in the Makefile's `test-live` order. Each starts from `reset_demo_state` (self-verifying) and keeps its service logs in `$BUVI_E2E_LOGS/<flow>/`.
3. **system:** `pytest -m system` against the live stack.
   - forced RLS on every service table;
   - the request role cannot bypass RLS;
   - the query principals are read-only (Postgres and MySQL);
   - the result bucket expires objects;
   - the IdP locks an account after repeated password failures.

`test-client` (step 2) type-checks the generated client against the contract, then drives the running gateway through it.

## Locally

```bash
make up                 # the compose stack (Postgres, Redis, NATS, Keycloak, Vault, MinIO, MailHog, MySQL)
make backend-e2e        # ~30 min; logs under $BUVI_E2E_LOGS (a temp dir by default)
```

Nothing else may be serving on ports 8000-8010, 8765 or 8766: each flow starts its own services, and refuses to start if another process already holds its port.

## Reading a failure

- The summary names the failed step and its log (`$BUVI_E2E_LOGS/<step>.log`). The last 25 lines are printed inline.
- **In CI:** download the `backend-e2e-logs` artifact. It holds every flow's per-service logs. The job also prints `docker compose logs` on failure.
- **A stage that timed out** logs `stage timed out` with `awaiting`: the innermost frames it was stuck in (ADR 0015). Start there.
- **Postgres logs lock waits longer than 1 s** (`log_lock_waits`), naming the blocking process. A client that died mid-transaction has its locks released after 60 s (`idle_in_transaction_session_timeout`).
