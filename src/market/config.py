from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='MARKET__', env_file='.env', extra='ignore', hide_input_in_errors=True
    )
    session_file: Path = Path('.secrets/cookies.txt')
    interval_seconds: float = Field(default=2, ge=1, le=3600)
