"""visualization-service configuration (12-factor). Stateless: no database, no outbound calls
except identity-service's JWKS for verifying service tokens."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SCOPE_VALIDATE = "visualization-service:validate"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISUALIZATION_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "visualization-service"
    log_level: str = "INFO"

    identity_url: str = "http://localhost:8001"
    service_token_issuer: str = "identity-service"  # noqa: S105 - issuer name

    @property
    def jwks_url(self) -> str:
        return f"{self.identity_url.rstrip('/')}/internal/v1/jwks.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
