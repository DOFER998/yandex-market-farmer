from pathlib import Path

from pydantic import Field, SecretStr

from src.common.config.types.base import BaseTypeConfig


class BotTypeConfig(BaseTypeConfig):
    owner_id: int = Field(gt=0)
    token: SecretStr | None = None
    token_file: Path | None = None

    def get_token(self) -> str:
        if self.token_file:
            return self.token_file.read_text().strip()
        if self.token:
            return self.token.get_secret_value()
        raise ValueError('Configure BOT__TOKEN_FILE or BOT__TOKEN')
