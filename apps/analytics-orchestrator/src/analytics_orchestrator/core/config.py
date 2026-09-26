"""Runtime configuration (12-factor). Development defaults match the compose stack; staging/prod
refuse every laptop-only value, including the scripted model provider."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SCOPE_PROXY = "analytics-orchestrator:proxy"
SCOPE_EXECUTE = "analytics-orchestrator:execute"
SCOPE_EVENTS = "analytics-orchestrator:events"
SCOPE_CONTEXT = "metadata-service:context"
SCOPE_QUERY_EXECUTE = "query-gateway:execute"
SCOPE_RESOLVE_PRINCIPAL = "identity-service:resolve-principal"
SCOPE_CHART_VALIDATE = "visualization-service:validate"
SCOPE_ARTIFACTS_WRITE = "dashboard-service:artifacts"
SCOPE_SEMANTIC_CONTEXT = "semantic-service:context"
#: worker-runtime writes aggregated `billing.usage.recorded` events (Phase A11).
SCOPE_USAGE_WRITE = "analytics-orchestrator:usage"
#: Seat count for `/billing/usage` (Phase A11).
SCOPE_DIRECTORY = "identity-service:directory"

RUN_REQUESTED_SUBJECT = "analytics.run.requested"
BILLING_USAGE_SUBJECT = "billing.usage.recorded"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANALYTICS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "analytics-orchestrator"
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
    query_gateway_url: str = "http://localhost:8003"
    visualization_url: str = "http://localhost:8006"
    dashboard_url: str = "http://localhost:8007"
    semantic_url: str = "http://localhost:8008"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name
    service_client_id: str = "analytics-orchestrator"
    service_client_secret: SecretStr = SecretStr("dev-analytics-orchestrator-secret")
    require_gateway_token: bool = False
    session_cookie_name: str = "buvi_session"

    redis_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    run_stream: str = "ANALYTICS"
    billing_stream: str = "BILLING"

    # --- ModelRouter (Section 23) ---------------------------------------------------------
    #: `scripted` is a deterministic, offline provider for development and tests only.
    #: `openai_compatible` is any server speaking OpenAI's `/chat/completions` (ADR 0022) --
    #: a self-hosted or gateway-fronted model, configured by the three settings below.
    llm_provider: Literal["anthropic", "openai_compatible", "scripted"] = "scripted"
    llm_model: str = "claude-opus-5"
    #: `openai_compatible` only: the API base (the part before `/chat/completions`) and its key.
    llm_base_url: str | None = None
    llm_api_key: SecretStr = SecretStr("")
    #: How structured output is requested. Downgrade if a server rejects `json_schema`; the schema
    #: is stated in the system prompt regardless, and Pydantic validation is the real gate.
    llm_response_format: Literal["json_schema", "json_object", "none"] = "json_schema"
    #: Used on provider error, timeout or refusal -- never silently for cost (Section 23).
    llm_fallback_model: str | None = "claude-opus-4-8"
    llm_max_tokens_per_call: int = Field(default=4_096, ge=256, le=32_000)
    #: Development only: simulated latency per scripted answer, so a live run can be interrupted.
    scripted_latency_seconds: float = Field(default=0.0, ge=0.0, le=30.0)

    # --- Budgets (Sections 10.3, 23): enforced before every call ----------------------------
    run_token_budget: int = Field(default=60_000, ge=1)
    tenant_daily_token_budget: int = Field(default=2_000_000, ge=1)
    stage_timeout_seconds: float = Field(default=90.0, gt=0)
    run_timeout_seconds: float = Field(default=300.0, gt=0)
    max_repair_attempts: int = Field(default=2, ge=0, le=2)

    query_max_rows: int = Field(default=1_000, ge=1)
    query_timeout_ms: int = Field(default=30_000, ge=100)
    #: A tenant at its query concurrency cap (429) is retried this many times, with
    #: exponential backoff from `query_capacity_backoff_seconds`, before the run fails.
    query_capacity_retries: int = Field(default=3, ge=0, le=10)
    query_capacity_backoff_seconds: float = Field(default=1.0, ge=0)
    context_max_tables: int = Field(default=8, ge=1, le=50)

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
        if self.llm_provider == "scripted":
            problems.append("llm_provider is the scripted development provider")
        if self.llm_provider == "openai_compatible":
            if not self.llm_base_url:
                problems.append("llm_provider is openai_compatible but llm_base_url is unset")
            if not self.llm_api_key.get_secret_value():
                problems.append("llm_provider is openai_compatible but llm_api_key is unset")
            if self.llm_base_url and self.llm_base_url.startswith("http://"):
                # A model call carries tenant schema and question text: it may not leave in clear.
                problems.append("llm_base_url is plaintext http (Section 24: TLS in transit)")
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
