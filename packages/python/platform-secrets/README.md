# platform-secrets

The shared secrets client (build spec Sections 8.1, 13.1, 24; Phase A3).

Postgres stores *references* (`secret_ref`, `idp_refresh_token_ref`); this package is
the only code that turns a reference into a value.

Contract:

- `SecretStore`: `write(ref, value)`, `read(ref)`, `delete(ref)`, `ping()`.
- `VaultSecretStore`: HashiCorp Vault KV v2 adapter. Errors never carry a Vault
  response body.
- `InMemorySecretStore`: for tests only. Services refuse it outside `dev`/`test`.
- `vault_kv2_path(mount, ref)`: the full Vault API path for a reference, e.g.
  `secret/data/tenants/<id>/datasources/<id>` (Section 8.2's `secret_ref` form).

A reference is a relative path of `[A-Za-z0-9_-]` segments. Anything else (`..`,
a leading `/`, empty segments) is refused before any request is made.

Consumers: identity-service (session refresh tokens, TOTP secrets) and
metadata-service (data-source credentials).
