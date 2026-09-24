# 0017. Replan Track B against the real backend and the Stitch screen set

## Context

Section 31's Track B (Phases B1–B7) was written before Track A existed. Track A finished with a
materially larger and differently-shaped API surface than the v6 spec's Phase B text assumed:

- Phase A10 added step-up/WebAuthn, tenant policies (`/admin/policies`), per-connection SQL
  grants (`/data-sources/{id}/sql-grants`), session management (`/me/sessions`), API keys
  (`/me/api-keys`), and MFA self-service (`/me/mfa`) — none of which the old Phase B1/B6 text
  named explicitly.
- Phase A11 added the entire notification/webhook/billing-usage surface
  (`/me/notifications`, `/admin/webhooks`, `/billing/usage`, `/billing/quotas`) — the old Phase
  B7 gestured at "notifications" but not webhooks or usage.
- `POST /billing/subscription` is a deliberate `_stub` in `api_gateway/domain/catalog.py`
  returning a documented `501` (no payment provider, no owning service — Section 31 post-GA
  backlog). No Phase B work may wire a real action to it.
- The old Phase B3 ("Developer SQL Editor") undersold the data-source/connection surface
  (create, test, sync, secret rotation, per-connection grants, table/column browse), which is a
  distinct, large piece of the developer workspace per Section 34.

Separately, the user supplied 15 pre-built UI screens via the `stitch` MCP server (project
`10440972999306255957`, "BuVi Enterprise BI Platform"), generated against a design system whose
tokens are near-identical to Section 5.1's (same hex palette, same Inter/JetBrains Mono pairing,
same "no shadows on resting surfaces, no gradients, no emoji" rules) but materially more
detailed at the component level (exact button/table/badge/code-editor specs, 48px top-nav height,
4px corner radius, breakpoint matrix). These screens are the literal visual contract for Track B
and must drive it, not the reverse.

## Decision

1. **Track B is replanned from 7 phases to 8**, so that Data Sources (connection management) and
   SQL Lab — two large, distinct pieces of developer-workspace surface, and two distinct Stitch
   screens — are no longer flattened into one phase. New phase list: B1 Auth+shell, B2 Client
   chat+dashboards, B3 Data sources & catalog, B4 SQL Lab, B5 Semantic management, B6 MCP
   governance, B7 Admin console, B8 Notifications+webhooks+guest share.
2. **Each phase names its literal Stitch screen(s)** by title (screens are addressed by title, not
   by a Claude-invented name) as the visual reference an implementer opens before writing the
   page. Three route groups have no matching Stitch screen (personal account settings under
   `/me/*`; tenant policies under `/admin/policies`; webhook admin under `/admin/webhooks`) — for
   those, the phase text names the closest existing screen's pattern to follow, per the user's
   own instruction, rather than inventing a new visual language.
3. **Section 5.1 is extended, not replaced**, with a short pointer to the Stitch project as the
   canonical pixel-level reference; the palette/token values are unchanged (the user was explicit:
   do not change the theme). The two happen to already agree, which is confirmed rather than
   assumed — 5.1's hex values and the Stitch `designMd` are identical to two decimal places.
4. **The "Usage & Quotas" Stitch screen shows UI for invoice reconciliation and a spend-cap that
   the backend does not implement** (billing/subscription is the `501` stub above). Phase B7 must
   render the parts backed by `GET /billing/usage` and `GET /billing/quotas` for real, and must
   not wire the invoice/spend-cap controls shown in the mock to any live action — consistent with
   Section 31.0's "do not implement frontend-only functionality" and the audit principle that a
   screen existing is not the same as a feature existing.
5. **Section 11 documents a real gap instead of inventing a fix for it.** The database's
   `Run.status` has a `cancelled` value, but the wire contract
   (`platform_contracts.AnalyticsRunEvent.status`) is `Literal["started","completed","failed"]` —
   a cancelled run currently reaches the browser as a plain `run.failed` event, distinguishable
   from a real failure only by matching the fixed message string "The run was cancelled." An
   earlier draft of this ADR and of Section 11 incorrectly assumed a `run.cancelled` wire event
   already existed; it does not, and Section 11 has been corrected to say so plainly. Widening the
   status literal and emitting it from the cancellation path is a small, additive backend change,
   noted as a Phase B2 prerequisite rather than performed under this ADR (Track A is otherwise
   frozen to bug fixes; this rewrite's scope is Track B planning, not Track A code).

   **Resolved in Phase B2.** `AnalyticsRunEvent.status` gained `"cancelled"`; `run_executor.py`'s
   `_fail()` and `conversation_service.py`'s `_end_without_execution()` (the queued-run cancel
   path, which had the same bug independently — it hardcoded the *event's* status to `"failed"`
   even when the *run's* own status was being set to `"cancelled"`) both emit the run's real
   terminal status instead of always `"failed"`. Migration `0003_run_events_cancelled_status`
   widens `analytics.run_events`'s DB check constraint. `web/next-app`'s chat UI now branches on
   `AnalyticsRunEvent.status` directly (Section 11's own rule) rather than the message-string
   fallback this section described.

## Consequences

- Track B's phase count changes from 7 to 8; CLAUDE.md's "Next up" line and Section 31.0 must be
  read against the new list, not the old one.
- No backend change follows from this ADR directly. Track A remains frozen except for bug fixes —
  the one exception is the already-flagged Phase B2 prerequisite above, resolved when B2 shipped.
- Auditor and billing_admin (Section 7.1) have no dedicated Stitch screens; Phase B7 reuses the
  Admin console's own components in a reduced, read-only configuration for them rather than
  building separate screens — their permission matrix is narrow enough that this is a
  configuration of one screen, not a new one.
