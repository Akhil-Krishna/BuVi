# @buvi/api-client

The typed client Track B imports (Phase A12). It is generated from `contracts/openapi/api-gateway.json`,
the only API surface a browser reaches.

- `src/schema.d.ts` is **generated**. Never edit it; run `scripts/gen-client.sh`. CI runs
  `scripts/gen-client.sh --check` and fails on drift from the contract.
- `src/index.ts` is a thin layer over [`openapi-fetch`](https://openapi-ts.dev/openapi-fetch/):
  - it sends credentials, because the session is an HttpOnly cookie (Section 6.1): the client
    never holds a token;
  - it adds an `Idempotency-Key` to every mutating request, so a retry is replayed by the
    gateway instead of performed twice;
  - `isApiError` and `stepUpMethod` handle the Section 21 error envelope and `STEP_UP_REQUIRED`.

```ts
import { createApiClient, stepUpMethod } from "@buvi/api-client";

const api = createApiClient({ baseUrl: "" }); // same origin in the browser
const { data, error } = await api.GET("/api/v1/me/notifications", {
  params: { query: { unread_only: true, limit: 20 } },
});
if (stepUpMethod(error)) {
  // prompt for MFA, then retry
}
```

`src/smoke.ts` drives the running gateway through the client. It is part of
`scripts/backend-e2e.sh`, which proves the client works.
