from pathlib import Path

from pydantic import Field

from src.common.config.types.base import BaseTypeConfig


class MarketTypeConfig(BaseTypeConfig):
    session_dir: Path = Path('.secrets/accounts')
    interval_seconds: float = Field(default=2, ge=1, le=3600)
