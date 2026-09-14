"""api-gateway configuration (12-factor). Dev defaults match the compose stack."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from api_gateway.domain.policies.rate_limit import BucketRule, RateLimitPolicy

SCOPE_INTROSPECT = "identity-service:introspect"
SCOPE_PROXY = "identity-service:proxy"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "api-gateway"
    log_level: str = "INFO"

    # --- Upstreams ------------------------------------------------------------
    #: Owning service -> base URL. Services join this map as their phases land.
    backend_urls: dict[str, str] = Field(
        default_factory=lambda: {"identity-service": "http://localhost:8001"}
    )
    upstream_connect_timeout_seconds: float = 3.0
    upstream_timeout_seconds: float = 30.0
    max_request_body_bytes: int = 1_048_576

    # --- Service auth (Section 6.3) --------------------------------------------
    service_token_url: str = "http://localhost:8001/internal/v1/oauth/token"  # noqa: S105 - URL
    service_client_id: str = "api-gateway"
    service_client_secret: SecretStr = SecretStr("dev-gateway-secret")

    # --- Credentials the gateway forwards for introspection ----------------------
    session_cookie_name: str = "buvi_session"

    # --- Rate limiting (Sections 5, 20, 24) ---------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    #: On a Redis outage, allow traffic (logged) rather than fail the whole API.
    rate_limit_fail_open: bool = True
    rate_auth_ip_capacity: int = 10
    rate_auth_ip_refill_per_second: float = 0.2
    rate_public_ip_capacity: int = 60
    rate_public_ip_refill_per_second: float = 1.0
    rate_user_capacity: int = 120
    rate_user_refill_per_second: float = 2.0
    rate_tenant_capacity: int = 1000
    rate_tenant_refill_per_second: float = 20.0

    #: Reverse proxies in front of the gateway whose `X-Forwarded-For` entry is
    #: trusted. 0 = use the socket peer (the gateway is the edge).
    trusted_proxy_hops: int = 0

    def rate_limit_policy(self) -> RateLimitPolicy:
        return RateLimitPolicy(
            auth_ip=BucketRule(
                "auth", "ip", self.rate_auth_ip_capacity, self.rate_auth_ip_refill_per_second
            ),
            public_ip=BucketRule(
                "public", "ip", self.rate_public_ip_capacity, self.rate_public_ip_refill_per_second
            ),
            user=BucketRule(
                "user", "user", self.rate_user_capacity, self.rate_user_refill_per_second
            ),
            tenant=BucketRule(
                "tenant", "tenant", self.rate_tenant_capacity, self.rate_tenant_refill_per_second
            ),
        )

    def assert_production_safe(self) -> None:
        if self.environment not in ("staging", "prod"):
            return
        if self.service_client_secret.get_secret_value().startswith("dev-"):
            raise RuntimeError(
                "Unsafe configuration: service_client_secret is the development value"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
