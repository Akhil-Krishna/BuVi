"""Runtime configuration (12-factor: everything from the environment).

No secret has a usable production default. Development defaults match
`infra/compose/docker-compose.dev.yml` so a developer can `make up` and run the
service without a `.env` file, and every one of them is obviously a throwaway.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Service scopes identity-service grants (Section 6.3).
SCOPE_INTROSPECT = "identity-service:introspect"
SCOPE_PROXY = "identity-service:proxy"
#: Append audit events on behalf of another service (Sections 7.3, 22; ADR 0004).
SCOPE_AUDIT_WRITE = "identity-service:audit"
#: Resolve a user's *current* principal for delegated work (Section 13, ADR 0006).
SCOPE_RESOLVE_PRINCIPAL = "identity-service:resolve-principal"

_DEV_GATEWAY_SECRET_SHA256 = hashlib.sha256(b"dev-gateway-secret").hexdigest()
_DEV_METADATA_SECRET_SHA256 = hashlib.sha256(b"dev-metadata-secret").hexdigest()
_DEV_QUERY_GATEWAY_SECRET_SHA256 = hashlib.sha256(b"dev-query-gateway-secret").hexdigest()
_DEV_ORCHESTRATOR_SECRET_SHA256 = hashlib.sha256(b"dev-analytics-orchestrator-secret").hexdigest()
_DEV_WORKER_SECRET_SHA256 = hashlib.sha256(b"dev-worker-runtime-secret").hexdigest()
_DEV_SECRET_HASHES = frozenset(
    {
        _DEV_GATEWAY_SECRET_SHA256,
        _DEV_METADATA_SECRET_SHA256,
        _DEV_QUERY_GATEWAY_SECRET_SHA256,
        _DEV_ORCHESTRATOR_SECRET_SHA256,
        _DEV_WORKER_SECRET_SHA256,
    }
)


class ServiceClient(BaseModel):
    """A registered workload allowed to use the client-credentials grant."""

    #: SHA-256 of the client secret. Secrets are high-entropy, so a fast hash suffices.
    secret_sha256: str
    #: audience -> scopes this client may request for it.
    audiences: dict[str, list[str]]
    #: Event-type prefixes this client may append through `/internal/v1/audit-events`.
    #: Empty means none: a service cannot forge another domain's audit rows.
    audit_event_prefixes: list[str] = Field(default_factory=list)


def _dev_service_clients() -> dict[str, ServiceClient]:
    return {
        "api-gateway": ServiceClient(
            secret_sha256=_DEV_GATEWAY_SECRET_SHA256,
            audiences={
                "identity-service": [SCOPE_INTROSPECT, SCOPE_PROXY],
                "metadata-service": ["metadata-service:proxy"],
                # Section 9 `/sql/execute` proxies here once a public SQL route exists.
                "query-gateway": ["query-gateway:execute"],
                "analytics-orchestrator": [
                    "analytics-orchestrator:proxy",
                    "analytics-orchestrator:events",
                ],
            },
        ),
        "metadata-service": ServiceClient(
            secret_sha256=_DEV_METADATA_SECRET_SHA256,
            audiences={"identity-service": [SCOPE_INTROSPECT, SCOPE_AUDIT_WRITE]},
            audit_event_prefixes=["connection."],
        ),
        "query-gateway": ServiceClient(
            secret_sha256=_DEV_QUERY_GATEWAY_SECRET_SHA256,
            audiences={
                "identity-service": [SCOPE_INTROSPECT, SCOPE_RESOLVE_PRINCIPAL],
                "metadata-service": ["metadata-service:query-policy"],
            },
        ),
        "analytics-orchestrator": ServiceClient(
            secret_sha256=_DEV_ORCHESTRATOR_SECRET_SHA256,
            audiences={
                "identity-service": [SCOPE_INTROSPECT, SCOPE_RESOLVE_PRINCIPAL],
                "metadata-service": ["metadata-service:context"],
                "query-gateway": ["query-gateway:execute"],
            },
        ),
        "worker-runtime": ServiceClient(
            secret_sha256=_DEV_WORKER_SECRET_SHA256,
            audiences={"analytics-orchestrator": ["analytics-orchestrator:execute"]},
        ),
    }


class Settings(BaseSettings):
    """identity-service settings."""

    model_config = SettingsConfigDict(
        env_prefix="IDENTITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "identity-service"
    log_level: str = "INFO"

    # --- Database -------------------------------------------------------
    # The request-path role is `buvi_app`, which cannot bypass RLS (Section 19).
    # Alembic runs as `buvi_migrator`; see `migrations/env.py`.
    database_dsn: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://buvi_app:devapp@localhost:5432/agentic_bi")
    )
    migration_dsn: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://buvi_migrator:devmigrator@localhost:5432/agentic_bi"
        )
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- OIDC / Keycloak (Section 6.1) ----------------------------------
    oidc_issuer: str = "http://localhost:8080/realms/buvi"
    oidc_client_id: str = "buvi-platform"
    oidc_client_secret: SecretStr = SecretStr("dev-client-secret")
    #: Public callback URL. The browser reaches identity-service through api-gateway.
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_scopes: str = "openid profile email"
    oidc_admin_base_url: str = "http://localhost:8080"

    # --- Session (Sections 6.1, 6.9) ------------------------------------
    session_cookie_name: str = "buvi_session"
    #: Section 6.1: HttpOnly, Secure, SameSite=Lax, scoped to the app path.
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_path: str = "/"
    session_cookie_domain: str | None = None
    #: Section 6.9 defaults: 12h idle, 7d absolute -- both enforced server-side.
    session_idle_timeout_hours: int = 12
    session_absolute_lifetime_days: int = 7
    #: Short-lived cookie holding the PKCE verifier between login and callback.
    oidc_transaction_cookie_name: str = "buvi_oidc_txn"
    oidc_transaction_ttl_seconds: int = 600

    # --- Secrets (Section 8.1: refresh tokens are stored by reference) ---
    vault_addr: str = "http://localhost:8200"
    vault_token: SecretStr = SecretStr("devroot")
    vault_mount: str = "secret"
    #: In-memory secret store instead of Vault. Test fixtures only; refused
    #: outside `dev`/`test` by the validator below.
    vault_use_memory_stub: bool = False

    # --- Email (Section 6.7 invitations; MailHog locally) ----------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str = "no-reply@buvi.example.com"
    #: Where an invitee lands to accept. Track B serves this route; until then
    #: the token in the email is consumed directly by the API.
    invitation_accept_url: str = "http://localhost:3000/invitations/accept"
    invitation_ttl_days: int = 7

    # --- MFA (Section 6.6) ----------------------------------------------
    totp_issuer: str = "BuVi"
    totp_valid_window: int = 1

    # --- API keys (Section 6.8) ------------------------------------------
    api_key_prefix: str = "sk_live_"

    # --- Service-to-service auth (Section 6.3) ----------------------------
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_token_key_id: str = "identity-service-1"  # noqa: S105 - key id
    #: PEM RSA private key. Unset => ephemeral key (dev/test only).
    service_token_private_key: SecretStr | None = None
    service_token_ttl_seconds: int = 300
    service_clients: dict[str, ServiceClient] = Field(default_factory=_dev_service_clients)
    #: Require a valid api-gateway service token on every `/api/v1` request.
    require_gateway_token: bool = False

    def assert_production_safe(self) -> None:
        """Fail fast on a configuration that is only safe on a laptop.

        Called during startup. A misconfigured production deployment must not
        boot quietly with development credentials or a stubbed secret store.
        """
        if self.environment not in ("staging", "prod"):
            return
        problems: list[str] = []
        if self.vault_use_memory_stub:
            problems.append("vault_use_memory_stub is on outside dev/test")
        if not self.session_cookie_secure:
            problems.append("session_cookie_secure is off")
        if self.oidc_client_secret.get_secret_value().startswith("dev-"):
            problems.append("oidc_client_secret is still the development value")
        if self.service_token_private_key is None:
            problems.append("service_token_private_key is unset (ephemeral signing key)")
        if not self.require_gateway_token:
            problems.append("require_gateway_token is off (service reachable around the gateway)")
        if any(c.secret_sha256 in _DEV_SECRET_HASHES for c in self.service_clients.values()):
            problems.append("a service client still uses the development secret")
        if self.vault_token.get_secret_value() == "devroot":
            problems.append("vault_token is still the development root token")
        if problems:
            raise RuntimeError(
                f"Unsafe configuration for {self.environment}: {'; '.join(problems)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read from the environment once."""
    return Settings()
