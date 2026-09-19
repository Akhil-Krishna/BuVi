# platform-testing

Shared pytest fixtures: testcontainers, factories, and demo principals.

Workspace member of the `buvi` uv workspace (Section 4 of the build spec), in the root `dev`
dependency group only, so it never ships in a service image. Imported by services as an explicit
workspace dependency, never by accidental cross-service import.

| Module | Contract |
|---|---|
| `platform_testing.mysql` | `sample_sales_mysql()`: a real MySQL 8.4 with the compose sample data; `mariadb()`: a real MariaDB the MySQL connectors must refuse (Phase A8) |
| `platform_testing.mcp` | `sample_mcp_app()`: a real MCP server (the official SDK's FastMCP, JSON or SSE mode) with read, write, failing and misbehaving tools; `serve()`; `make_tls()`: a throwaway CA and certificate. `python -m platform_testing.mcp [port]` runs the live flow's server (Phase A9) |
| `platform_testing.webauthn` | `SoftAuthenticator`: a software WebAuthn authenticator (ES256, `none` attestation) that produces the JSON a browser sends, with knobs for a wrong origin and a regressed counter (Phase A10) |
