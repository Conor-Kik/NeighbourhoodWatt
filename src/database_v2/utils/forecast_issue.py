"""Resolve the optimiser issue in force at a simulation replay position."""

from datetime import UTC, datetime, timedelta

from backend_v2.community_optimiser.optimiser_constants import OPTIMISER_PERIOD_HOURS


def forecast_issue_at(
    start: datetime, end: datetime, moment: datetime
) -> datetime | None:
    """Match the engine's intervals anchored at request start, end-exclusive."""
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    moment = (
        moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    )
    if not start <= moment < end:
        return None
    period = timedelta(hours=OPTIMISER_PERIOD_HOURS)
    return start + ((moment - start) // period) * period
