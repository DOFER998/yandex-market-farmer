"""Wheel mission observations; completion does not assert an attributed wallet credit."""

from typing import Literal

from pydantic import Field

from src.market.models import Model

FREE_CLICK_MISSIONS = frozenset(
    {
        'wheel_open_help_2026_v2',
        'fortune_wheel_market_subscribe_tg_channel_test_october_2026',
    }
)


class WheelMission(Model):
    mission_id: str
    title: str = ''
    status: str
    reward: int = Field(ge=0)
    accumulated: int | None = Field(default=None, ge=0)
    goal: int | None = Field(default=None, ge=0)


class WheelMissionAttempt(Model):
    mission_id: str
    state: Literal['unsupported', 'unavailable', 'already_completed', 'completed', 'unconfirmed']
    balance_before: int | None = Field(default=None, ge=0)
    balance_after: int | None = Field(default=None, ge=0)
