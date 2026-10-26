from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from database_v2.models.data_models import (
    ForecastEmissionsSnapshot,
    ForecastPriceSnapshot,
    ForecastPvSnapshot,
)


def test_price_snapshot_rejects_misaligned_series() -> None:
    target = datetime(2026, 9, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="equal lengths"):
        ForecastPriceSnapshot(
            run_time_utc=target,
            location_name="brisbane",
            target_times_utc=[target, target + timedelta(minutes=30)],
            buy_tariffs_aud=[0.25],
            sell_tariffs_aud=[0.05, 0.06],
            forecast_sources=["aemo", "historical"],
        )


def test_emissions_snapshot_rejects_misaligned_series() -> None:
    target = datetime(2026, 9, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="equal lengths"):
        ForecastEmissionsSnapshot(
            run_time_utc=target,
            location_name="brisbane",
            target_times_utc=[target, target + timedelta(minutes=30)],
            carbon_intensities_t_per_mwh=[0.7],
        )


def test_emissions_snapshot_rejects_negative_intensity() -> None:
    target = datetime(2026, 9, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ForecastEmissionsSnapshot(
            run_time_utc=target,
            location_name="brisbane",
            target_times_utc=[target],
            carbon_intensities_t_per_mwh=[-0.1],
        )


def test_pv_snapshot_rejects_misaligned_series() -> None:
    target = datetime(2026, 9, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="equal lengths"):
        ForecastPvSnapshot(
            available_at_utc=target,
            house_config_id="some-config",
            location_name="Brisbane",
            target_times_utc=[target, target + timedelta(minutes=30)],
            pv_kw=[1.5],
        )


def test_pv_snapshot_rejects_negative_pv() -> None:
    target = datetime(2026, 9, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ForecastPvSnapshot(
            available_at_utc=target,
            house_config_id="some-config",
            location_name="Brisbane",
            target_times_utc=[target],
            pv_kw=[-0.1],
        )


@pytest.mark.parametrize("sources", [["aemo"], ["aemo", "invalid"]])
def test_price_snapshot_rejects_invalid_sources(sources):
    target = datetime(2026, 9, 24, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ForecastPriceSnapshot(
            run_time_utc=target,
            location_name="Brisbane",
            target_times_utc=[target, target + timedelta(minutes=30)],
            buy_tariffs_aud=[0.1, 0.2],
            sell_tariffs_aud=[0.1, 0.2],
            forecast_sources=sources,
        )
