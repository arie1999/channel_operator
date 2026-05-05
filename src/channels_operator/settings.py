"""Environment-based settings.

Loads from .env via pydantic-settings. Per-channel bot tokens in
channel.yaml override these defaults; SaaS multi-tenant deployments
will prefer per-channel tokens entirely. These env-var values are the
v1 fallback so a single-channel operator does not have to put tokens
in YAML.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    editor_bot_token: str = ""
    publisher_bot_token: str = ""
    operator_telegram_user_id: int = 0
    database_url: str = "sqlite:///data/channels.db"

    # Optional filter: when non-empty, the runtime operates on only this
    # channel. Empty = run all channels with non-empty telegram_channel_id.
    # Phase 6 enables concurrent multi-channel operation by default.
    active_channel_id: str = ""


settings = Settings()
