from pydantic import Field, SecretStr
from sqlalchemy import URL

from src.common.config.types.base import BaseTypeConfig


class PostgresTypeConfig(BaseTypeConfig):
    host: str = '127.0.0.1'
    port: int = Field(default=5432, gt=0, le=65535)
    user: str = 'ymf'
    password: SecretStr
    database: str = 'ymf'

    @property
    def dsn(self) -> str:
        return URL.create(
            'postgresql+asyncpg',
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.database,
        ).render_as_string(hide_password=False)
