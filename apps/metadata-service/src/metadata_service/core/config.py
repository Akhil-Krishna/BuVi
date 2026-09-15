"""Runtime configuration (12-factor: everything from the environment).

Development defaults match `infra/compose/docker-compose.dev.yml`; every one of them
is an obvious throwaway, and `assert_production_safe` refuses to boot staging/prod
with any of them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Scope api-gateway needs to proxy to this service (Section 6.3).
SCOPE_PROXY = "metadata-service:proxy"
#: Scope this service needs to record audit events in identity-service (ADR 0004).
SCOPE_AUDIT_WRITE = "identity-service:audit"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    """metadata-service settings."""

    model_config = SettingsConfigDict(
        env_prefix="METADATA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "metadata-service"
    log_level: str = "INFO"

    # --- Database (Section 19: request path is `buvi_app`, which cannot bypass RLS) ---
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

    # --- identity-service: introspection, service tokens, audit (Sections 6.3, 22) ---
    identity_url: str = "http://localhost:8001"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "metadata-service"
    service_client_secret: SecretStr = SecretStr("dev-metadata-secret")
    #: Require a valid api-gateway service token on every `/api/v1` request.
    require_gateway_token: bool = False
    session_cookie_name: str = "buvi_session"
    audit_delivery_attempts: int = Field(default=3, ge=1, le=10)

    # --- Secrets (Section 13.1: credentials live only in Vault) ----------------------
    vault_addr: str = "http://localhost:8200"
    vault_token: SecretStr = SecretStr("devroot")
    vault_mount: str = "secret"
    #: In-memory secret store. Test fixtures only; refused outside dev/test.
    vault_use_memory_stub: bool = False

    # --- Connectors (Sections 13.1, 15, 20) ----------------------------------------
    #: Hosts exempt from the private-address egress block (Section 15: "explicitly
    #: allow-listed internal service"). Development allows the compose sample database.
    connector_allowed_internal_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1", "sample-sales-db"]
    )
    connector_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    connector_statement_timeout_ms: int = Field(default=15_000, ge=100, le=300_000)
    catalog_max_tables: int = Field(default=5_000, ge=1)
    catalog_max_columns: int = Field(default=100_000, ge=1)

    @property
    def service_token_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/oauth/token"

    @property
    def jwks_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/jwks.json"

    def assert_production_safe(self) -> None:
        """Fail fast on a configuration that is only safe on a laptop."""
        if self.environment not in ("staging", "prod"):
            return
        problems: list[str] = []
        if self.vault_use_memory_stub:
            problems.append("vault_use_memory_stub is on outside dev/test")
        if self.vault_token.get_secret_value() == "devroot":
            problems.append("vault_token is still the development root token")
        if self.service_client_secret.get_secret_value().startswith("dev-"):
            problems.append("service_client_secret is still the development value")
        if not self.require_gateway_token:
            problems.append("require_gateway_token is off (service reachable around the gateway)")
        loopback = _LOOPBACK_HOSTS & {
            h.strip().lower() for h in self.connector_allowed_internal_hosts
        }
        if loopback:
            problems.append(f"connector_allowed_internal_hosts allows loopback {sorted(loopback)}")
        if problems:
            raise RuntimeError(
                f"Unsafe configuration for {self.environment}: {'; '.join(problems)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read from the environment once."""
    return Settings()
