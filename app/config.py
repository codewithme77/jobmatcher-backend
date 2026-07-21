from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = Field(default="JobMatcher API", validation_alias=AliasChoices("APP_NAME", "app_name"))
    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "debug"))
    database_url: str = Field(default="sqlite:///./jobmatcher.db", validation_alias=AliasChoices("DATABASE_URL", "database_url"))

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
