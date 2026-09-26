# 0023. The developer/admin nav is a superset of the client nav

## Status

Accepted. Deviates deliberately from how Section 5.2's two nav screens were read in Phase B1.

## Context

Section 5.2 names two Stitch nav screens — "Top Navigation - Client Role View" and "Top Navigation -
Developer & Admin View". Phase B1 implemented those as two **mutually exclusive** lists:
`navItemsFor` returned either `CLIENT_ITEMS` (Chat, Dashboards) or `DEVELOPER_ADMIN_ITEMS` (Data
Sources, SQL Lab, Semantic, MCP, Users, Policies, Audit, Billing, Webhooks), never a union. Tests
pinned that reading in place: `login.spec.ts` asserted a `developer` session shows **no** Chat link,
and `nav-items.test.ts` said an auditor "must not be shown the chat/dashboard-pin nav".

That is wrong against Section 7.1. A `developer` holds:

```
artifact:read, catalog:read, chat:use, dashboard:pin, dashboard:read,
dashboard:share, data:manage, run:debug, semantic:manage, sql:execute
```

`chat:use`, `dashboard:read`, `dashboard:pin` and `dashboard:share` — all four. Every server-side
check allowed a developer to use Chat and Dashboards, `/chat` and `/dashboards` rendered fine if
typed by hand, and only the nav hid them. So the product forbade by omission what the authorization
model explicitly granted.

It also matters for the actual job. A developer connects a data source, and the natural next step is
to ask a question of it, get a chart, and pin that chart to a dashboard. Sending them to SQL Lab
instead is a worse path for the same goal — and B4's "Send to Chat" already assumes a developer can
*reach* chat, which they could not.

## Decision

`DEVELOPER_ADMIN_ITEMS` is now `[...CLIENT_ITEMS, ...DEVELOPER_ADMIN_ONLY]`. The wide nav starts
with Chat and Dashboards and continues into the developer/admin surfaces.

One subtlety this turns on: `navVariantFor` must test the **developer-only** subset, not the
combined list. A pure `client` holds `chat:use`, so checking the combined list would match them and
hand them the wide chrome — the opposite of the intent. `DEVELOPER_ADMIN_ONLY` is kept as a separate
constant for exactly that reason, with a comment saying so at the definition.

`navItemsFor`'s per-item permission filter is unchanged and still does the real work, so:

| Session | Sees |
|---|---|
| `client` | Chat, Dashboards (client variant — unchanged) |
| `developer` | Chat, Dashboards, Data Sources, SQL Lab, Semantic |
| `org_admin` | the above plus Users, Policies, Audit, Billing, Webhooks, MCP |
| `auditor` | Dashboards, Data Sources, Audit — **not** Chat, since they lack `chat:use` |

The auditor case is the check that this is permission-driven rather than role-name-driven: they gain
read-only Dashboards, which Section 7.1 grants them (`dashboard:read`, `artifact:read`), and still
never see Chat.

## Why this is a deviation worth making

Section 5.2's rule is "match the named screen's layout, density, and component treatment" — it is a
*visual* contract, and this change does not alter the nav's appearance, only which permission-gated
entries can appear in it. Reading two screens as two disjoint permission sets was an inference, not
something 5.2 states, and it contradicted Section 7.1, which is normative for who may do what.
Where the two conflict, the permission matrix wins.

Section 7.4 also applies: nav visibility is UX only, never the control. Nothing here weakens a
check — every route still re-checks server-side, and this ADR changes no permission, policy, or
endpoint.

## Consequences

- `nav-items.test.ts`: the auditor's expected list gains `Dashboards`; two cases added — a real
  developer's full list, and a pure client still getting the client variant (the regression this
  design could plausibly cause).
- `login.spec.ts`: the developer assertion inverts from "Chat absent" to "Chat and Dashboards
  visible". Verified in a real browser against live Keycloak; all three login specs pass.
- No backend change. No change to `CLIENT_ITEMS`, so the client experience is untouched.

## Not covered by this ADR

Making Chat reachable is not the same as the rest of the developer workflow being built. Still
missing, and each needing real work:

- a tile on a dashboard does not show the SQL that produced it — `ArtifactResponse` exposes
  `source_refs` (data source id + table names) but **not** `validated_sql`, so this needs a backend
  field before any UI;
- "edit this chart through the agent" cannot reopen the originating conversation: the artifact
  carries `conversation_id`, but Section 9's chat surface is create-and-post only
  (`POST /conversations`, `POST /conversations/{id}/messages`) with **no** GET for a conversation or
  its messages;
- manual chart building without chat does not exist (ADR 0020 chose composer-seeding over a
  skip-regeneration path);
- `TileGrid` is explicitly read-only — no drag, resize, or tile removal.
