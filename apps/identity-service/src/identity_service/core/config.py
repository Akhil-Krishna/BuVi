"""Runtime configuration (12-factor: everything from the environment).

No secret has a usable production default. Development defaults match
`infra/compose/docker-compose.dev.yml` so a developer can `make up` and run the
service without a `.env` file, and every one of them is obviously a throwaway.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    oidc_redirect_uri: str = "http://localhost:8001/api/v1/auth/callback"
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
