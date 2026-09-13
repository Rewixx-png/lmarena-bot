"""Application configuration loaded from environment / .env file."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    bot_token: str = ""
    channel_id: str = ""      # "@channelname" or numeric id; empty -> channel notifications off
    admin_chat_id: str = ""   # personal chat id/@username for direct notifications; empty -> off

    # --- Monitoring ---
    check_interval_seconds: int = 180
    request_timeout_seconds: float = 30.0
    max_retries: int = 3

    # --- Diff thresholds ---
    elo_change_threshold: float = 5.0
    rank_change_threshold: int = 3
    stats_notify_top_n: int = 40  # only report stat updates for models ranked <= this

    # --- Storage ---
    database_path: str = "arena.db"

    # --- Sources (primary RSC, fallback full HTML) ---
    rsc_url: str = "https://arena.ai/leaderboard?_rsc=1"
    html_url: str = "https://arena.ai/leaderboard"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def parse_chat_id(value: str):
    """Normalize a chat/channel id from .env: numeric string -> int, '@name' -> str."""
    value = (value or "").strip()
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value
