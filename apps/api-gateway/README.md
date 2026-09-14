# api-gateway

The public API surface of the BuVi platform and the only service the Next.js BFF
calls (build spec Sections 3, 6.3, 9, 20, 21, 22, 24).

| | |
|---|---|
| Owner | platform / edge |
| State | stateless; Redis holds rate-limit counters only |
| Port | 8000 |
| Health | `GET /health/live`; `GET /health/ready` (identity-service required, Redis reported) |
| API | `/api/v1` — contract `contracts/openapi/api-gateway.json` |
| Dependencies | identity-service (introspection, service tokens), Redis, owning services as they land |
| Runbook | [`docs/runbooks/api-gateway.md`](../../docs/runbooks/api-gateway.md) |
| Decisions | [ADR 0003](../../docs/adr/0003-phase-a2-api-gateway.md) |

## What happens to a request

1. A fresh `request_id` is minted (a client-supplied `X-Request-ID` is ignored).
2. Per-IP token bucket — strict `auth` tier for login-shaped routes, `public` otherwise → `429`.
3. Public route? Skip to 7.
4. The session cookie or `Authorization: Bearer <api key>` is resolved to a Principal by
   identity-service introspection, so revocation takes effect immediately → `401`/`403`.
5. Per-user and per-tenant token buckets → `429`.
6. Coarse Section 9 checks: permission, role, Section 7.3 step-up → `403`.
7. Owning service not built yet → `501 NOT_IMPLEMENTED` (with `available_in_phase`);
   otherwise proxied with a service JWT in `X-Service-Authorization`.

Resource-tenant checks (Section 7.2) always happen in the owning service. The
gateway is never the only trust boundary.

## Configuration (`GATEWAY_*`)

| Variable | Default (dev) |
|---|---|
| `GATEWAY_BACKEND_URLS` | `{"identity-service": "http://localhost:8001"}` |
| `GATEWAY_SERVICE_TOKEN_URL` | `http://localhost:8001/internal/v1/oauth/token` |
| `GATEWAY_SERVICE_CLIENT_ID` / `_SECRET` | `api-gateway` / `dev-gateway-secret` (refused in staging/prod) |
| `GATEWAY_REDIS_URL` | `redis://localhost:6379/0` |
| `GATEWAY_RATE_LIMIT_FAIL_OPEN` | `true` |
| `GATEWAY_RATE_{AUTH_IP,PUBLIC_IP,USER,TENANT}_CAPACITY` | 10 / 60 / 120 / 1000 |
| `GATEWAY_RATE_*_REFILL_PER_SECOND` | 0.2 / 1 / 2 / 20 |
| `GATEWAY_TRUSTED_PROXY_HOPS` | `0` (the gateway is the edge) |
| `GATEWAY_MAX_REQUEST_BODY_BYTES` | `1048576` |

## Local development

```bash
make up && make migrate && make seed
make dev            # identity-service :8001
make dev-gateway    # api-gateway :8000
make test-login     # end-to-end through the gateway, including a 429 burst
make contracts      # re-export contracts/openapi/*.json after an API change
```
