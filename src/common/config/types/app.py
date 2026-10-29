from pathlib import Path

from src.common.config.types.base import BaseTypeConfig


class AppTypeConfig(BaseTypeConfig):
    debug: bool = False
    heartbeat_file: Path = Path('.secrets/runtime/heartbeat')
