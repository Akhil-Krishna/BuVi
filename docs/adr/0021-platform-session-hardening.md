# 0021. The application carries the platform database's session hardening, not the server

## Status

Accepted. Closes the mitigation half of Phase C1's Phase A5 crash-resume entry requirement; the
root-cause half stays open (see "What this does not close").

## Context

ADR 0014 and ADR 0015 left the Phase A5 crash-resume stall in a deliberately partial state: Phase
A12 shipped *detection* (the stage timeout logs the frames it was awaiting, dev/CI Postgres runs
with `log_lock_waits=on`, `deadlock_timeout=1s` and `idle_in_transaction_session_timeout=60s`), not
a fix. A resumed run stalled once with no I/O until its stage timeout and has not reproduced —
1 failure in 15 runs.

ADR 0014 also named why the detection was not enough:

> Production has no equivalent: there is no Terraform yet, and platform-database sessions set no
> `lock_timeout` or `statement_timeout`. Only customer-facing connections (metadata connectors,
> query-gateway executors) set those per session.

So dev was *better protected than production* for a failure class that is not dev-specific. Any
client that disappears without a FIN — node loss, kernel panic, a network partition, a stateful
load balancer or connection pooler dropping state — leaves its backend idle in transaction holding
row locks until TCP keepalive detection. `tcp_keepalives_idle` defaults to the system value
(7200 s on Linux) and `idle_in_transaction_session_timeout` defaults to `0`, so an abruptly lost
worker can hold row locks for hours and a resumed run blocks on them exactly as observed.

Phase C1's entry requirement asks for "the production equivalents of the dev-only mitigations".
The obvious reading is "put the settings in Terraform" — but `infra/terraform/` is still empty
(`.gitkeep` only), and a managed database (RDS, Cloud SQL) is frequently not a server whose
config this project owns at all.

## Decision

**Every service sets the hardening on its own connections, as asyncpg startup parameters, instead
of relying on the database server's configuration.**

`PLATFORM_SESSION_SETTINGS` in each service's `infrastructure/db/session.py` is passed to
`create_async_engine` as `connect_args={"server_settings": ...}`:

| Setting | Value | Why |
|---|---|---|
| `lock_timeout` | `15s` | A waiter blocked on a lock a dead holder never released fails fast with a lock error, instead of blocking until its stage timeout. This is the setting that closes the A5 stall class. |
| `idle_in_transaction_session_timeout` | `60s` | A client that died mid-transaction releases its locks within a minute. Matches what dev compose already set, now true everywhere. |
| `tcp_keepalives_idle` / `_interval` / `_count` | `60` / `10` / `3` | Detect a client that vanished without a FIN in ~90 s rather than the Linux default of 2 h+. |

All five are `USERSET` GUCs, so asyncpg can send them in the startup packet and they hold for
every session, verified rather than assumed (`SHOW` on a connection built by the real
`create_engine`).

### Why the application and not the server

1. **It is true in every environment by construction.** There is no Terraform to forget to apply,
   and no dependence on owning the database server's config — the asymmetry ADR 0014 flagged
   cannot recur, because the setting travels with the code that needs it.
2. **It survives a managed database.** RDS/Cloud SQL parameter groups are a separate change
   process; a startup parameter is not.
3. **Migrations are unaffected.** Alembic connects through `migration_dsn` on a separate engine, so
   a long migration does not inherit the request path's `lock_timeout`.

A server-side setting is still welcome as defence in depth once Terraform exists; it is no longer
load-bearing.

### Why the constant is duplicated per service

It lives in all eight services' own `session.py` rather than in a new `platform-db` package. The
same eight files already duplicate `db_pool_size`, `db_max_overflow` and `db_echo` — Section 4
makes each service's session module its own, and extracting a package would mean a new workspace
member plus sixteen `pyproject.toml` lines plus a lockfile re-resolution, putting all eight
services' startup at risk for a five-entry dict. The comment in each copy says to extract a narrow
`platform-db` package if a third setting ever needs to change in lockstep, so the drift risk is
recorded where someone editing one copy will see it.

## Consequences

Proven by `tests/system/test_platform_session_hardening.py` (four `system`-marked tests, run by
`scripts/backend-e2e.sh`):

- the real `create_engine` applies all five settings (intervals compared as intervals, since
  Postgres normalises `60s` to `1min`);
- the shipped `lock_timeout` is finite and fail-fast — asserted from the constant, so a fast
  behavioural test can never hide a wrong shipped value. Regressing it to `0` fails this test,
  checked by temporarily doing so;
- a waiter blocked behind a held lock raises `LockNotAvailableError` quickly, and demonstrably
  *because* it waited and timed out rather than never contending;
- the A5 scenario itself: the holder is terminated mid-transaction with
  `pg_terminate_backend` and the waiter then proceeds instead of inheriting a lock nobody will
  release.

All eight hardened services' existing suites pass unchanged (identity-service's 260 included), and
identity-service reports `database: ok` on `/health/ready` in a real process.

## What this does not close

Phase C1's A5 entry requirement asks for **both** the production mitigations *and* "the root cause,
named from evidence". This ADR delivers the first. The second cannot honestly be claimed yet:

- the stall is 1-in-15 and has not reproduced since the detection shipped, so there is no incident
  log naming the awaited frames or the blocking PID;
- what these tests establish is that the *suspected mechanism is sufficient* to produce the
  observed symptom — a terminated holder does block a waiter indefinitely without `lock_timeout`,
  and fails fast with it. That is stronger than the speculation ADR 0014 recorded, but it is not a
  post-mortem of the specific historical incident.

**Therefore the A5 item stays on the carried-forward list**, narrowed: what remains is confirming
attribution on the next recurrence (CI keeps every flow's service logs as the `backend-e2e-logs`
artifact), not building a mitigation. If the stall does not recur under the new settings, that is
itself evidence — but it should be recorded after a meaningful number of runs, not assumed now.
