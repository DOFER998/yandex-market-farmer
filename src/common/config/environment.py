from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.common.config.types import (
    AppTypeConfig,
    BotTypeConfig,
    MarketTypeConfig,
    PostgresTypeConfig,
)


class Environment(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_nested_delimiter='__',
        extra='ignore',
        hide_input_in_errors=True,
    )
    bot: BotTypeConfig
    postgres: PostgresTypeConfig
    app: AppTypeConfig = Field(default_factory=AppTypeConfig)
    market: MarketTypeConfig = Field(default_factory=MarketTypeConfig)


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Load once at startup; importing a module does not require production secrets."""
    return Environment()
