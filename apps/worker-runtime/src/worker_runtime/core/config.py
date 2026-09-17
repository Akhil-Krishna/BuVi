"""worker-runtime configuration (12-factor)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

RUN_REQUESTED_SUBJECT = "analytics.run.requested"
SCOPE_EXECUTE = "analytics-orchestrator:execute"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "worker-runtime"
    log_level: str = "INFO"

    nats_url: str = "nats://localhost:4222"
    run_stream: str = "ANALYTICS"
    durable_name: str = "worker-runtime-analytics"
    #: Longer than a run's 300s budget: a live execution is never redelivered, a dead one is.
    ack_wait_seconds: float = Field(default=360.0, gt=0)
    max_deliver: int = Field(default=5, ge=1)
    heartbeat_seconds: float = Field(default=15.0, gt=0)
    fetch_timeout_seconds: float = Field(default=5.0, gt=0)
    retry_base_seconds: float = Field(default=5.0, gt=0)

    orchestrator_url: str = "http://localhost:8004"
    execute_timeout_seconds: float = Field(default=330.0, gt=0)
    identity_url: str = "http://localhost:8001"
    service_client_id: str = "worker-runtime"
    service_client_secret: SecretStr = SecretStr("dev-worker-runtime-secret")

    @property
    def service_token_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/oauth/token"

    def assert_production_safe(self) -> None:
        if self.environment in (
            "staging",
            "prod",
        ) and self.service_client_secret.get_secret_value().startswith("dev-"):
            raise RuntimeError(
                "Unsafe configuration: service_client_secret is the development value"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
