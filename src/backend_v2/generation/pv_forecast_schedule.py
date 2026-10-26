"""Causal availability and target coverage for daily PV forecast cycles."""

from datetime import datetime, timedelta

from backend_v2.weather.fetch_weather import (
    ECMWF_24H_HORIZON_AVAILABILITY_DELAY,
    latest_available_ecmwf_ensemble_init_time,
)

FORECAST_STEP = timedelta(minutes=30)
FORECAST_HORIZON = timedelta(hours=24)


def available_pv_cycle_at(run_time_utc: datetime) -> datetime:
    """Return when the newest usable ECMWF cycle became available."""
    cycle = latest_available_ecmwf_ensemble_init_time(run_time_utc)
    return (cycle + ECMWF_24H_HORIZON_AVAILABILITY_DELAY).to_pydatetime()


def pv_cycle_windows(
    start_utc: datetime, end_utc: datetime
) -> dict[datetime, tuple[datetime, datetime]]:
    """Map each needed availability time to its half-hour target interval."""
    windows: dict[datetime, tuple[datetime, datetime]] = {}
    run_time = start_utc
    while run_time < end_utc:
        available_at = available_pv_cycle_at(run_time)
        end_target = run_time + FORECAST_HORIZON
        if available_at in windows:
            first_target, _ = windows[available_at]
            windows[available_at] = (first_target, end_target)
        else:
            windows[available_at] = (run_time, end_target)
        run_time += FORECAST_STEP
    return windows
