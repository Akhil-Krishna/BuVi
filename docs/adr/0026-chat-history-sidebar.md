# 0026. A chat history sidebar, and the two backend endpoints it needed

## Status
Accepted. Closes the gap CLAUDE.md carried since B2: "no conversation-history sidebar the Stitch
mock shows... Section 9's chat surface is create-and-post only."

## Decision
`GET /conversations` (the caller's own, newest first) and `GET /conversations/{id}/messages`,
both `chat:use`. `ConversationSidebar` lists them; clicking one loads its messages into the same
`Turn` shape the live view already renders (`turn.reply` -- already added for ADR 0025's
conversational replies -- doubles as "whatever text the assistant stored", so no new render path).
Historical charts are not re-fetched; this is chat history, not artifact replay. A new
conversation is auto-titled from its opening message (Claude/ChatGPT-style), or every sidebar
entry read "Untitled".

## A real access-control gap, found writing the tests
`OwnsConversation` (the existing router dependency) only checks the resource is this *tenant*'s,
which was fine when the only reader was the conversation's own creator posting to it. Listing
messages exposed it: any same-tenant user could read anyone else's private chat. Fixed in
`ConversationService.list_messages` with an owner check (`created_by == principal.user_id`),
`404` on mismatch -- Section 7.2's cross-tenant convention, applied one level down: never confirm
a conversation exists to someone who doesn't own it. Four integration tests, including one proving
a same-tenant non-owner gets exactly the cross-tenant tenant's 404, not a 403.

## Consequences
`contracts/openapi/{analytics-orchestrator,api-gateway}.json` and the generated TS client
regenerated (additive only, diffed to confirm). All service suites, mypy and ruff pass.
