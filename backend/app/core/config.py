from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://ledgerflow:ledgerflow@localhost:55432/ledgerflow"
    redis_url: str = "redis://127.0.0.1:56379/0"
    rabbitmq_url: str = "amqp://ledgerflow:ledgerflow@127.0.0.1:5673/"
    sandbox_mode: bool = False
    jwt_secret: str = "sandbox-only-ledgerflow-signing-key-change-outside-sandbox"
    jwt_issuer: str = "ledgerflow"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    provider_alpha_url: str = "http://127.0.0.1:9001"
    provider_beta_url: str = "http://127.0.0.1:9002"
    provider_timeout_seconds: float = 3.5
    webhook_receiver_url: str = "http://127.0.0.1:9003/events"
    webhook_secret: str = "sandbox-webhook-signing-secret"
    risk_review_threshold_minor: int = 200_000
    risk_decline_threshold_minor: int = 1_000_000
    risk_velocity_limit: int = 100
    otel_exporter_otlp_endpoint: str = ""
    log_level: str = "INFO"

    @model_validator(mode="after")
    def production_secrets(self) -> "Settings":
        if not self.sandbox_mode and (
            self.jwt_secret.startswith("sandbox-") or len(self.jwt_secret) < 32
        ):
            raise ValueError("Set a strong JWT_SECRET or explicitly enable SANDBOX_MODE.")
        if not self.sandbox_mode and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be enabled outside sandbox mode.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
