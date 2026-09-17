"""Runtime configuration (12-factor). Development defaults match the compose stack; every
one of them is refused in staging/prod by `assert_production_safe`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Section 6.3 / 13: the scope a caller needs on `POST /internal/v1/queries`.
SCOPE_EXECUTE = "query-gateway:execute"
#: Read a stored `analytics_run` result behind its handle (dashboard-service, Section 13).
SCOPE_RESULTS = "query-gateway:results"
#: Scope this service needs to load a data source's query policy from metadata-service.
SCOPE_QUERY_POLICY = "metadata-service:query-policy"
#: Scope this service needs to resolve a delegated user (Section 13, ADR 0006).
SCOPE_RESOLVE_PRINCIPAL = "identity-service:resolve-principal"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _default_purpose_callers() -> dict[str, list[str]]:
    return {"sql_editor": ["api-gateway"], "analytics_run": ["analytics-orchestrator"]}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUERY_GATEWAY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "query-gateway"
    log_level: str = "INFO"

    # --- Platform database (Section 19: request path is `buvi_app`) --------------------
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

    # --- Other services (Section 6.3) -----------------------------------------------------
    identity_url: str = "http://localhost:8001"
    metadata_url: str = "http://localhost:8002"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "query-gateway"
    service_client_secret: SecretStr = SecretStr("dev-query-gateway-secret")
    session_cookie_name: str = "buvi_session"
    #: Which calling service may request which purpose (ADR 0005).
    purpose_callers: dict[str, list[str]] = Field(default_factory=_default_purpose_callers)
    #: Services allowed to send `on_behalf_of` for purpose `analytics_run` (Section 13).
    delegating_callers: list[str] = Field(default_factory=lambda: ["analytics-orchestrator"])
    #: Services allowed to read stored results through `POST /internal/v1/results/read`.
    result_readers: list[str] = Field(default_factory=lambda: ["dashboard-service"])
    policy_cache_ttl_seconds: float = Field(default=30.0, ge=0, le=600)

    # --- Secrets (Section 13.1) --------------------------------------------------------------
    vault_addr: str = "http://localhost:8200"
    vault_token: SecretStr = SecretStr("devroot")
    vault_mount: str = "secret"
    vault_use_memory_stub: bool = False

    # --- Result handles (Sections 8.5, 13, 24, 28) --------------------------------------------
    result_store_endpoint: str = "localhost:9000"
    result_store_access_key: SecretStr = SecretStr("minioadmin")
    result_store_secret_key: SecretStr = SecretStr("minioadmin")
    result_store_secure: bool = False
    result_store_bucket: str = "query-results"
    #: Section 13: "result handle in object storage with TTL (default 24h)". Lifecycle
    #: rules expire whole days, so the handle lives at least this long, at most one day more.
    result_ttl_days: int = Field(default=1, ge=1, le=30)
    result_store_use_memory_stub: bool = False

    # --- Connectors and limits (Sections 13, 15, 20) ------------------------------------------
    connector_allowed_internal_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1", "sample-sales-db"]
    )
    connector_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    connector_pool_max_size: int = Field(default=5, ge=1, le=50)
    connector_pool_idle_seconds: float = Field(default=300.0, ge=10)
    default_max_rows: int = Field(default=10_000, ge=1)
    max_rows_limit: int = Field(default=50_000, ge=1)
    default_timeout_ms: int = Field(default=30_000, ge=100)
    max_timeout_ms: int = Field(default=120_000, ge=100)
    max_result_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_sql_length: int = Field(default=50_000, ge=100)
    #: Section 20: one noisy tenant cannot starve another's queries.
    tenant_max_concurrent_queries: int = Field(default=4, ge=1)

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
        if self.vault_use_memory_stub:
            problems.append("vault_use_memory_stub is on outside dev/test")
        if self.result_store_use_memory_stub:
            problems.append("result_store_use_memory_stub is on outside dev/test")
        if self.vault_token.get_secret_value() == "devroot":
            problems.append("vault_token is still the development root token")
        if self.service_client_secret.get_secret_value().startswith("dev-"):
            problems.append("service_client_secret is still the development value")
        if self.result_store_secret_key.get_secret_value() == "minioadmin":
            problems.append("result store credentials are the development values")
        if not self.result_store_secure:
            problems.append("result_store_secure is off")
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
    return Settings()
