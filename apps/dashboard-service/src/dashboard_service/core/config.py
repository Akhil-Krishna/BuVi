"""dashboard-service configuration (12-factor). Development defaults match the compose stack;
staging/prod refuse every laptop-only value."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SCOPE_PROXY = "dashboard-service:proxy"
#: Store an artifact produced by a run (analytics-orchestrator's `persist_artifact`).
SCOPE_ARTIFACTS_WRITE = "dashboard-service:artifacts"
SCOPE_VALIDATE = "visualization-service:validate"
SCOPE_RESULTS = "query-gateway:results"
SCOPE_AUDIT_WRITE = "identity-service:audit"

TILE_PINNED_SUBJECT = "dashboard.tile.pinned"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DASHBOARD_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "dashboard-service"
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
    visualization_url: str = "http://localhost:8006"
    query_gateway_url: str = "http://localhost:8003"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "dashboard-service"
    service_client_secret: SecretStr = SecretStr("dev-dashboard-service-secret")
    require_gateway_token: bool = False
    session_cookie_name: str = "buvi_session"
    #: Services allowed to store artifacts (Section 8.9: runs produce them).
    artifact_writers: list[str] = Field(default_factory=lambda: ["analytics-orchestrator"])

    nats_url: str = "nats://localhost:4222"
    dashboard_stream: str = "DASHBOARD"

    # --- Share links and exports (Sections 7.3, 8.6; Phase A10) ---------------------------
    #: Where a guest opens a link (the Track B app); the token is appended.
    share_base_url: str = "http://localhost:3000/share/"
    share_link_default_hours: int = Field(default=72, ge=1)
    #: "Time-boxed": no link outlives this.
    share_link_max_hours: int = Field(default=168, ge=1, le=720)
    max_active_share_links: int = Field(default=20, ge=1)
    #: Reading more result rows than this is an export (Section 7.3): it needs a fresh
    #: step-up, and a guest snapshot omits the tile's data.
    export_step_up_rows: int = Field(default=10_000, ge=1)

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
