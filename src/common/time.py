from datetime import UTC, datetime


def now() -> datetime:
    """Timezone-aware UTC for persisted timestamps."""
    return datetime.now(UTC)
