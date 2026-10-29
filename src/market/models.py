from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Model(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')


class AuthContext(Model):
    sk: SecretStr
    login: str
    version: str
    glue: str
    language: str = 'ru'


class Game(Model):
    game_id: str
    token: SecretStr = Field(exclude=True, repr=False)


class Mission(Model):
    mission_id: str
    status: str
    reward: int = Field(ge=0)
    accumulated: int | None = Field(default=None, ge=0)
    goal: int | None = Field(default=None, ge=0)


class Hub(Model):
    games: list[Game]
    missions: list[Mission]


class Level(Model):
    level: str
    requiredScore: int = Field(ge=0)
    rewardPoints: int = Field(ge=0)
    achievedToken: SecretStr = Field(default=SecretStr(''), exclude=True, repr=False)
    isAchieved: bool

    @property
    def claim_name(self) -> str | None:
        import re

        if self.isAchieved and self.rewardPoints > 0 and self.achievedToken.get_secret_value():
            match = re.fullmatch(r'RESULT_LEVEL_([A-Z0-9_]+)', self.level)
            if match:
                return match[1].lower()
        return None


class DailyStatus(Model):
    available: bool
    next_reward_at: datetime | None = None


class DailyClaim(Model):
    state: Literal['unavailable', 'submitted_unverified']
    next_reward_at: datetime | None = Field(default=None, exclude=True)


class GameClaim(Model):
    claimed: list[str]
    unconfirmed: list[str]
