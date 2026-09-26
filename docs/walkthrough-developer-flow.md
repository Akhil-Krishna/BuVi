# Walkthrough: connect a data source as a developer

A ready-made Postgres database exists for this, separate from `sample-sales-db` so you can practise
the whole connect flow from scratch.

## The data source you're connecting

Created on the existing sample-Postgres container (`buvi-dev-sample-sales-db-1`, host port **5433**):

| Setting | Value |
|---|---|
| Database | `retail_demo` |
| Schema | `retail` |
| Tables | `stores` (4), `categories` (4), `items` (8), `sales` (1,952) |
| Data range | 2025-01-01 → 2026-06-30, every 9 days, all stores × all items |
| Read-only user | `buvi_reader` |
| Password | `dev-reader-password` |

`buvi_reader` has `SELECT` only — verified: `DELETE FROM retail.sales` returns
`permission denied for table sales`. Same least-privilege pattern `sample_sales` uses.

To recreate it from scratch, re-run the SQL in
[`infra/compose/sample-sales-init/`](../infra/compose/sample-sales-init/)-style form, or ask for the
script again.

---

## Steps

Start everything first: `./scripts/run-local.sh` → open http://localhost:3000

### 1. Sign in as the developer

Click **Continue with SSO** → Keycloak login → `demo-developer` / `Demo-Passw0rd!23`.

You should now see **Chat · Dashboards · Data Sources · SQL Lab · Semantic** in the top nav.
(Chat and Dashboards are new — see ADR 0023; previously the developer nav hid them.)

### 2. Enrol MFA — required, do this before step 4

Click your email (top right) → **Account** → **Add authenticator app**. Scan the secret into any
TOTP app, enter the 6-digit code, **Confirm**.

**Why:** saving connection credentials is a step-up operation (Section 7.3 —
`POST /data-sources/{id}/secret` is `data:manage, step-up`). Without an enrolled factor, step 4
refuses and tells you to enrol one. Do it now rather than mid-flow.

### 3. Create the connection

**Data Sources** → **Add Data Source**:

| Field | Value | |
|---|---|---|
| Name | `retail-demo` | required |
| Engine | `PostgreSQL` | already the default |
| Display label | `Retail Demo (Postgres)` | **required** |
| Database name | `retail_demo` | required |
| Allowed schemas | type `retail` then press **Enter** | **required**, at least one |

All four are required. Leaving Display label blank (easy to do — it looks optional) fails with
*"Name, display label, database name, and at least one schema are required."*

**Allowed schemas is a tag input, not a text box.** There is no "Add schema" button — that string is
the box's *placeholder*. Type `retail` and press **Enter** (or comma); it becomes a removable tag.
The helper text under it says the same. If you type it and click straight to the submit button it
still commits on blur, but Enter is the reliable move.

Then click **Save data source**. The row appears as **`pending`**. That is correct and not a
failure: creating the row deliberately does not activate it.

> If you see a stray `adsfgh` row in `pending` from an earlier attempt, that's a leftover of exactly
> this step — it never got credentials. Safe to ignore, or remove it.

### 4. Set the credentials (step-up prompt appears here)

Click the `retail-demo` row to expand it. The **Connection credentials** form is already there —
there is no button to reveal it. Note there is **no Database field**: the database name came from
step 3 and is shown above as read-only detail.

| Field | Value | Note |
|---|---|---|
| Host | `localhost` | |
| Port | `5433` | **defaults to 5432 — you must change it** |
| Username | `buvi_reader` | |
| Password | `dev-reader-password` | |
| SSL mode | `disable` | **defaults to `require` — you must change it** |

Click **Save credentials** → the MFA prompt appears → enter your TOTP code → **Verify**.

Both defaults will silently fail against this database if left alone: port 5432 is the *platform*
Postgres, not the sample one, and `require` will fail on a container with no TLS configured.

`disable` is correct *here only* because this is a loopback container. `verify-full` is the
production requirement and is still a Phase C1 entry item (ADR 0011) — it is not proven live yet.

The password goes straight to Vault as a `secret_ref`. It is never returned to the browser, never
logged, and never appears in an error response (Section 37). Re-expanding the row shows
**Update credentials**, never the stored value.

### 5. Test the connection

**Test connection**. On success the status flips `pending` → **`active`**.

This is what activates it — testing is how a connection *leaves* `pending`, which is why Test isn't
disabled while pending.

### 6. Sync the catalog

**Sync catalog**. It reads table and column metadata (not data) into the `metadata` schema. The
table browser then lists `stores`, `categories`, `items`, `sales` with their columns.

### 7. Use it in Chat

**Chat** → pick `retail-demo` as the data source → ask something like
*"monthly net sales by store for 2025"*. You'll see the live execution trace, then a chart, then
**Pin to dashboard** → an existing dashboard or a new one.

### 8. Use it in SQL Lab — this needs one extra step

SQL Lab will refuse with a grant error until an **org_admin** grants you access to this specific
connection:

1. Sign in as `demo-admin` / `Demo-Passw0rd!23` (separate browser profile, or sign out)
2. **Data Sources** → expand `retail-demo` → **sql:execute grants** → pick `demo-developer` → grant
3. Back as `demo-developer`, SQL Lab can now query it

---

## Your access question, answered precisely

> "developers are connecting databases through connection string, so access will be handled right?
> i have doubts in that"

Partly yes, and one part will surprise you. Four separate mechanisms:

**1. The credential itself — fully handled.** Whatever you type in step 4 goes to Vault; the app
stores only a `secret_ref` pointer. It is never in frontend code, logs, error bodies, or Git.
`scripts/test_query_gateway.py` actively asserts the password and username never appear in any
response body.

**2. Connecting ≠ being allowed to query it. This is the surprise.** A developer with `data:manage`
can create, credential, test and sync a connection they **cannot then run ad-hoc SQL against**. SQL
Lab checks a *per-connection* grant:

```python
# query_service.py:259
# Section 7.1: `sql:execute` "(per-connection grant)" for everyone but org_admin.
if command.purpose is Purpose.SQL_EDITOR
   and not _grant_exempt(principal)
   and not await self._policies.has_sql_grant(tenant_id, policy.data_source_id, user_id):
    raise SqlGrantRequiredError()
```

`_grant_exempt` is `org_admin` or platform operator. And `sql-grants` endpoints are **`role
org_admin`** — so a developer cannot grant it to themselves. That's deliberate separation of
duties: holding "I can wire up a database" is not the same as "I can read everything in it".

**3. Chat does *not* require that per-connection grant.** Note `purpose is Purpose.SQL_EDITOR` — the
agent path uses a different purpose, so a developer can analyse a source in Chat that they cannot
hand-query in SQL Lab. The guardrails there are different, not absent: the LLM's SQL must pass the
query-gateway validator (Section 13) before executing, and the data source must be active and
tenant-owned. Worth knowing, because it means "no SQL Lab grant" is not the same as "no access to
the data".

**4. Tenant isolation is always separate and always on.** Every endpoint taking a resource id
composes a permission check with a tenant-ownership check; a cross-tenant id returns `404`, not
`403`, so ids can't be enumerated (Section 7.2).

---

## MCP for developers

`demo-developer` does **not** hold `mcp:manage` by default — only `org_admin` does. It isn't
missing; it's a tenant policy:

As `demo-admin` → **Policies** → tick **"Developers can manage MCP servers"** → **Save changes**
(step-up). That flips `developer_can_manage_mcp`, which adds `PERM_MCP_MANAGE` to every developer
in the tenant (`tenant_policy.py:48`). The developer's **MCP** nav link then appears on their next
request.
