"""semantic-service configuration (12-factor). Development defaults match the compose stack;
staging/prod refuse every laptop-only value."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SCOPE_PROXY = "semantic-service:proxy"
#: Approved metrics and dimensions for the Flow's `resolve_semantics` (analytics-orchestrator).
SCOPE_CONTEXT = "semantic-service:context"
SCOPE_CATALOG_LOOKUP = "metadata-service:catalog-lookup"
SCOPE_AUDIT_WRITE = "identity-service:audit"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEMANTIC_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "semantic-service"
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
    metadata_url: str = "http://localhost:8002"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "semantic-service"
    service_client_secret: SecretStr = SecretStr("dev-semantic-service-secret")
    require_gateway_token: bool = False
    session_cookie_name: str = "buvi_session"
    #: Services allowed to read the approved semantic context.
    context_readers: list[str] = Field(default_factory=lambda: ["analytics-orchestrator"])

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
        if problems:
            raise RuntimeError(
                f"Unsafe configuration for {self.environment}: {'; '.join(problems)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
