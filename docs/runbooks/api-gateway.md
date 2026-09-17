# Runbook: api-gateway

**Owner:** platform / edge · **Pages on:** error rate > 1% over 5 min, readiness failing (Section 22.1)

## Health

- `GET /health/live` — process up.
- `GET /health/ready` — `503` when identity-service is unreachable (nothing protected can be
  authenticated). Redis being down reports `degraded` but stays `200` while fail-open is on.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Spike of `502 UPSTREAM_UNAVAILABLE` on every protected route | identity-service down, or the gateway cannot get a service token | Check identity-service `/health/ready`; check `GATEWAY_SERVICE_CLIENT_SECRET` matches identity's registered `secret_sha256`. Users see 502, never a misleading 401. |
| `504 UPSTREAM_TIMEOUT` | Owning service slow | Check that service; tune `GATEWAY_UPSTREAM_TIMEOUT_SECONDS` only as a stopgap. |
| `429 RATE_LIMITED` spike, `details.scope=ip` on auth routes | Credential stuffing or a misbehaving client | Expected protection (Section 24). Identify the IP from logs by `request_id`; block at the edge if hostile. |
| `429` with `scope=tenant` for a legitimate tenant | Tenant outgrew defaults | Raise `GATEWAY_RATE_TENANT_CAPACITY`/`_REFILL_PER_SECOND`; record the change. |
| Rate limiting silently not applied | Redis outage with fail-open | Logs show `rate limiter unavailable`; restore Redis. Set `GATEWAY_RATE_LIMIT_FAIL_OPEN=false` during an active attack. |
| Audit rows show the gateway's IP | Deployed behind another proxy with `TRUSTED_PROXY_HOPS=0` | Set `GATEWAY_TRUSTED_PROXY_HOPS` to the number of trusted proxies in front. |
| identity-service answers `401 Service authentication required` | Service token rejected (clock skew, rotated signing key, wrong audience) | Check clock sync; the verifier re-fetches JWKS on unknown key ids. |

## Operations

- **Bring a new backend online:** add it to `GATEWAY_BACKEND_URLS`, register the gateway as a service
  client for that audience in identity-service (`<service>:proxy` scope), remove `available_in_phase`
  from its catalog rows, run `make contracts`, deploy.
- **Rotate the gateway's service client secret:** register the new SHA-256 in identity-service
  alongside the old one, deploy the gateway with the new secret, then remove the old hash.
- **Contracts:** `make contracts-check` (CI `contracts` job) fails on drift and on breaking changes
  without a major version bump.

## Idempotency-Key (ADR 0008)

- Records are Redis keys `idem:{tenant}:{principal}:{sha256}`. They are pending for at most the upstream timeout plus 30 s, then completed for `GATEWAY_IDEMPOTENCY_TTL_SECONDS` (24 h).
- **A burst of `503 IDEMPOTENCY_UNAVAILABLE`:** Redis is unreachable. Keyed writes are refused by design; restore Redis.
- **A client stuck on `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`:** its first request is still running upstream, or a gateway replica died mid-request. The lock expires on its own; do not delete records to "unstick" it.
- **Clearing one caller's records** (for example after a data repair): `redis-cli --scan --pattern 'idem:<tenant>:<user>:*' | xargs redis-cli del`.

## Deploy / rollback

Stateless: roll the deployment; roll back by redeploying the previous image. No migrations.
