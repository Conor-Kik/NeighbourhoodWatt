from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, create_autospec

import pandas as pd
import pytest
from pydantic import BaseModel

from backend_v2.aemo.aemo_archive_client import AemoArchiveClient
from backend_v2.aemo.aemo_emissions_service import (
    AemoEmissionsService,
    IncompleteEmissionsDataError,
)
from database_v2.models.request_models import Location, NemRegion

BRISBANE = Location(
    location_name="brisbane",
    latitude=-27.47,
    longitude=153.03,
    timezone="Australia/Brisbane",
    nem_region=NemRegion.QLD,
)


def mock_archive_client() -> MagicMock:
    return create_autospec(AemoArchiveClient, instance=True)


def test_realised_emissions_aggregate_generation_by_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_utc = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    dispatch = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": datetime(2026, 8, 20, 4, 35),
                "DUID": "COAL",
                "INTERVENTION": 0,
                "TOTALCLEARED": 75.0,
            },
            {
                "SETTLEMENTDATE": datetime(2026, 8, 20, 4, 35),
                "DUID": "SOLAR",
                "INTERVENTION": 0,
                "TOTALCLEARED": 25.0,
            },
        ]
    )
    service = AemoEmissionsService(mock_archive_client(), use_lightgbm=False)
    monkeypatch.setattr(
        service,
        "_get_generator_emissions_factors",
        lambda _: {"COAL": 0.8, "SOLAR": 0.0},
    )
    monkeypatch.setattr(
        service,
        "_get_duid_region_map",
        lambda _: {"COAL": "QLD1", "SOLAR": "QLD1"},
    )
    monkeypatch.setattr(service, "_compile_dispatchload", lambda *_: dispatch)

    rows = service.calculate_realised_emissions(
        BRISBANE, start_utc, start_utc + timedelta(minutes=5)
    )

    assert len(rows) == 1
    assert isinstance(rows[0], BaseModel)
    assert rows[0].timestamp_utc == start_utc
    assert rows[0].location_name == "brisbane"
    assert rows[0].carbon_intensity_t_per_mwh == pytest.approx(0.6)


def test_realised_emissions_reject_incomplete_dispatch_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_utc = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    service = AemoEmissionsService(mock_archive_client(), use_lightgbm=False)
    incomplete = pd.DataFrame(
        {
            "INTERVAL_START": [datetime(2026, 8, 20, 4, 30)],
            "intensity_t_per_mwh": [0.8],
        }
    )
    monkeypatch.setattr(service, "_load_realised_emissions", lambda **_: incomplete)

    with pytest.raises(IncompleteEmissionsDataError) as error:
        service.calculate_realised_emissions(
            BRISBANE, start_utc, start_utc + timedelta(minutes=10)
        )
    assert str(error.value) == (
        "Cannot use this date range for brisbane: AEMO emissions data is incomplete "
        "on 2026-08-20 (AEMO market time). "
        "Choose a date range that avoids these dates."
    )


def dispatch_interval(
    interval_start: datetime, generation_mwh: float, factor: float
) -> dict:
    emissions_tco2e = generation_mwh * factor
    return {
        "INTERVAL_START": interval_start,
        "generation_mwh": generation_mwh,
        "emissions_tco2e": emissions_tco2e,
        "intensity_t_per_mwh": emissions_tco2e / generation_mwh,
    }


def dispatch_history_rows(snapshot: datetime, days: int = 9) -> pd.DataFrame:
    rows = []
    for index in range(days * 48 * 6):  # 6 five-minute DISPATCH intervals per half hour
        interval_start = snapshot - timedelta(days=days) + timedelta(minutes=5 * index)
        # Recent data deliberately differs and must be excluded by the delay.
        factor = (
            0.8
            if interval_start.hour < 12
            or interval_start >= snapshot - timedelta(days=2)
            else 0.4
        )
        rows.append(
            dispatch_interval(interval_start, generation_mwh=10.0, factor=factor)
        )
    return pd.DataFrame(rows)


def emissions_service(
    monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame
) -> tuple[AemoEmissionsService, MagicMock]:
    service = AemoEmissionsService(mock_archive_client(), use_lightgbm=False)
    loader = MagicMock(return_value=frame)
    monkeypatch.setattr(service, "_load_realised_emissions", loader)
    return service, loader


def test_historical_profile_varies_by_target_without_recent_data(monkeypatch):
    snapshot = datetime(2026, 7, 2, 10)
    service, loader = emissions_service(monkeypatch, dispatch_history_rows(snapshot))
    start = datetime(2026, 7, 2, tzinfo=UTC)
    rows = service.calculate_forecast_emissions(
        BRISBANE, start, start + timedelta(days=1)
    )
    assert len(rows) == 48
    assert all(len(row.target_times_utc) == 48 for row in rows)
    assert rows[0].carbon_intensities_t_per_mwh == [0.8] * 4 + [0.4] * 24 + [0.8] * 20
    kwargs = loader.call_args.kwargs
    assert kwargs["start_market"] == snapshot - timedelta(days=9)
    assert kwargs["end_market"] == snapshot - timedelta(days=2) + timedelta(hours=23.5)


def test_recent_unit_data_cannot_supply_missing_history(monkeypatch):
    snapshot = datetime(2026, 7, 2, 10)
    service, _ = emissions_service(monkeypatch, dispatch_history_rows(snapshot, days=2))
    start = datetime(2026, 7, 2, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="Insufficient historical emissions"):
        service.calculate_forecast_emissions(
            BRISBANE, start, start + timedelta(minutes=30)
        )


def test_missing_slot_is_not_replaced_with_zero_or_persistence(monkeypatch):
    snapshot = datetime(2026, 7, 2, 10)
    frame = dispatch_history_rows(snapshot)
    half_hour = frame["INTERVAL_START"].dt.floor("30min")
    frame = frame[~((half_hour.dt.hour == 12) & (half_hour.dt.minute == 0))]
    service, _ = emissions_service(monkeypatch, frame)
    start = datetime(2026, 7, 2, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="missing 1 half-hour"):
        service.calculate_forecast_emissions(
            BRISBANE, start, start + timedelta(minutes=30)
        )


@pytest.mark.parametrize(
    ("start_utc", "end_utc", "message"),
    [
        (
            datetime(2026, 8, 19, 18, 30),
            datetime(2026, 8, 19, 19, 0, tzinfo=UTC),
            "start_utc must be timezone-aware",
        ),
        (
            datetime(2026, 8, 19, 18, 30, tzinfo=UTC),
            datetime(2026, 8, 19, 18, 30, tzinfo=UTC),
            "end_utc must be after start_utc",
        ),
        (
            datetime(2026, 8, 19, 18, 35, tzinfo=UTC),
            datetime(2026, 8, 19, 19, 0, tzinfo=UTC),
            "30-minute boundary",
        ),
    ],
)
def test_forecast_emissions_validate_window(
    start_utc: datetime, end_utc: datetime, message: str
) -> None:
    service = AemoEmissionsService(mock_archive_client(), use_lightgbm=False)

    with pytest.raises(ValueError, match=message):
        service.calculate_forecast_emissions(BRISBANE, start_utc, end_utc)
