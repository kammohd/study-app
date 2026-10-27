"""Settings, read from environment variables (or a local .env file)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str = ""      # only the backend holds this; it bypasses row-level security
    gemini_api_key: str = ""           # from Secret Manager on Cloud Run; empty = AI endpoints answer 503
    vapid_private_key: str = ""        # PEM, from Secret Manager; signs web-push so push services accept us
    vapid_subject: str = "mailto:noreply@users.noreply.github.com"
    cron_token: str = ""               # shared secret Cloud Scheduler sends to /internal/nudge; empty = endpoint closed
    daily_ai_limit: int = 20
    cors_origins: str = "https://kammohd.github.io"


def get_settings() -> Settings:
    return Settings()
