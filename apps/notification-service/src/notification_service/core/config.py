"""notification-service configuration (12-factor). Development defaults match the compose stack;
staging/prod refuse every laptop-only value."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SCOPE_PROXY = "notification-service:proxy"
SCOPE_AUDIT_WRITE = "identity-service:audit"
SCOPE_DIRECTORY = "identity-service:directory"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATION_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "notification-service"
    log_level: str = "INFO"

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

    identity_url: str = "http://localhost:8001"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "notification-service"
    service_client_secret: SecretStr = SecretStr("dev-notification-service-secret")
    require_gateway_token: bool = False
    session_cookie_name: str = "buvi_session"

    # --- Event consumption (Section 18.1) -----------------------------------------------
    nats_url: str = "nats://localhost:4222"
    #: Off in tests that drive the dispatcher directly.
    consumers_enabled: bool = True
    durable_prefix: str = "notification-service"
    fetch_batch: int = Field(default=10, ge=1, le=100)
    fetch_timeout_seconds: float = Field(default=2.0, gt=0)
    ack_wait_seconds: float = Field(default=120.0, gt=0)
    max_deliver: int = Field(default=10, ge=1)
    retry_seconds: float = Field(default=10.0, gt=0)

    # --- Email (Section 3: notification-service owns email) -----------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str = "BuVi <notifications@buvi.local>"

    # --- Webhooks (Section 15) ------------------------------------------------------------
    #: Internal hosts exempt from the public-address rule, and the only hosts that may use
    #: plain HTTP. Loopback here is refused in staging/prod.
    egress_allowed_internal_hosts: list[str] = Field(default_factory=list)
    webhook_timeout_seconds: float = 10.0
    webhook_connect_timeout_seconds: float = 5.0
    webhook_attempts: int = Field(default=3, ge=1, le=5)
    webhook_backoff_seconds: float = Field(default=2.0, ge=0)
    max_webhooks_per_tenant: int = Field(default=10, ge=1)

    vault_addr: str = "http://localhost:8200"
    vault_token: SecretStr = SecretStr("devroot")
    vault_mount: str = "secret"
    #: In-memory secret store. Test fixtures only; refused outside dev/test.
    vault_use_memory_stub: bool = False

    @property
    def service_token_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/oauth/token"

    @property
    def jwks_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/jwks.json"

    def assert_production_safe(self) -> None:
        if self.environment not in ("staging", "prod"):
            return
        problems: list[str] = []
        if self.service_client_secret.get_secret_value().startswith("dev-"):
            problems.append("service_client_secret is still the development value")
        if not self.require_gateway_token:
            problems.append("require_gateway_token is off (service reachable around the gateway)")
        if self.vault_use_memory_stub:
            problems.append("vault_use_memory_stub is on outside dev/test")
        if self.vault_token.get_secret_value() == "devroot":
            problems.append("vault_token is still the development root token")
        loopback = {"localhost", "127.0.0.1", "::1"}
        if loopback & {h.strip().lower() for h in self.egress_allowed_internal_hosts}:
            problems.append("egress_allowed_internal_hosts allow-lists loopback")
        if problems:
            raise RuntimeError(
                f"Unsafe configuration for {self.environment}: {'; '.join(problems)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
