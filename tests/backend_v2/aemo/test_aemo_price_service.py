from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock, create_autospec

import pandas as pd
import pytest
from pydantic import BaseModel, ValidationError

from backend_v2.aemo.aemo_archive_client import AemoArchiveClient
from backend_v2.aemo.aemo_constants import AemoArchiveTable
from backend_v2.aemo.aemo_price_service import AemoPriceService
from database_v2.models.request_models import Location, NemRegion

BRISBANE = Location(
    location_name="brisbane",
    latitude=-27.47,
    longitude=153.03,
    timezone="Australia/Brisbane",
    nem_region=NemRegion.QLD,
)


def mock_archive_client(frame: pd.DataFrame) -> MagicMock:
    client = create_autospec(AemoArchiveClient, instance=True)
    client.load_archive_table.return_value = frame.copy()
    client.load_interval_table.side_effect = AssertionError(
        "Unexpected NEMOSIS request in this test."
    )
    return client


def test_dispatch_prices_use_monthly_archive() -> None:
    frame = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": "2026/07/01 10:05:00",
                "REGIONID": "QLD1",
                "INTERVENTION": 0,
                "RRP": 100.0,
            },
            {
                "SETTLEMENTDATE": "2026/07/01 10:05:00",
                "REGIONID": "NSW1",
                "INTERVENTION": 0,
                "RRP": 110.0,
            },
        ]
    )
    client = mock_archive_client(frame)
    service = AemoPriceService(client, use_lightgbm=False)

    rows = service._compile_dispatch_prices(
        "QLD1", datetime(2026, 7, 1, 10), datetime(2026, 7, 1, 10, 30)
    )

    assert len(rows) == 1
    client.load_archive_table.assert_called_once()
    assert client.load_archive_table.call_args.kwargs["table"] == (
        AemoArchiveTable.DISPATCHPRICE
    )


def price_row(
    snapshot: datetime, lead: int, price: float, minutes: int, sequence: int = 1
) -> dict:
    return {
        "PREDISPATCHSEQNO": sequence,
        "RUNNO": 1,
        "PERIODID": lead + 1,
        "REGIONID": "QLD1",
        "INTERVENTION": 0,
        "DATETIME": snapshot + timedelta(minutes=30 * (lead + 1)),
        "LASTCHANGED": snapshot + timedelta(minutes=minutes),
        "RRP": price,
    }


@pytest.mark.parametrize("location_name", ["brisbane", "Murrumba Downs"])
def test_price_forecast_uses_revision_within_publication_grace(location_name) -> None:
    snapshot = datetime(2026, 8, 20, 10)
    frame = pd.DataFrame(
        [
            price_row(snapshot, 0, price, offset)
            for offset, price in [(-5, 100.0), (4, 200.0), (5, 300.0)]
        ]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    row = service._build_run(
        data=service._clean_predispatch_prices(frame),
        location=BRISBANE.model_copy(update={"location_name": location_name}),
        region="QLD1",
        snapshot_time=snapshot,
        forecast_count=1,
    )
    assert row.sell_tariffs_aud == [0.2]
    assert row.location_name == location_name


def test_price_forecast_rejects_target_without_an_available_revision() -> None:
    snapshot = datetime(2026, 8, 20, 10)
    previous = snapshot - timedelta(minutes=30)
    frame = pd.DataFrame(
        [
            price_row(snapshot, 0, 100, 1, sequence=2),
            price_row(snapshot, 1, 200, 5, sequence=2),
            price_row(snapshot, 2, 300, 1, sequence=2),
            price_row(previous, 2, 999, 1, sequence=1),
        ]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    with pytest.raises(RuntimeError, match="target 2026-08-20T10:30:00"):
        service._build_run(
            data=service._clean_predispatch_prices(frame),
            location=BRISBANE,
            region="QLD1",
            snapshot_time=snapshot,
            forecast_count=3,
        )


def test_price_forecast_uses_historical_missing_tail() -> None:
    snapshot = datetime(2026, 8, 20, 10)
    frame = pd.DataFrame(
        [
            price_row(snapshot, 0, 100, 1),
            price_row(snapshot, 1, 200, 1),
            price_row(snapshot - timedelta(days=1, minutes=-60), 0, 300, 1),
            price_row(snapshot - timedelta(days=1, minutes=-90), 0, 400, 1),
        ]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)

    row = service._build_run(
        data=service._clean_predispatch_prices(frame),
        location=BRISBANE,
        region="QLD1",
        snapshot_time=snapshot,
        forecast_count=4,
    )

    assert row.sell_tariffs_aud == [0.1, 0.2, 0.3, 0.4]
    assert row.forecast_sources == ["aemo", "aemo", "historical", "historical"]


def test_price_forecast_rejects_late_tail_target() -> None:
    snapshot = datetime(2026, 8, 20, 10)
    frame = pd.DataFrame(
        [
            price_row(snapshot, 0, 100, 1),
            price_row(snapshot, 1, 200, 5),
        ]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)

    with pytest.raises(RuntimeError, match="target 2026-08-20T10:30:00"):
        service._build_run(
            data=service._clean_predispatch_prices(frame),
            location=BRISBANE,
            region="QLD1",
            snapshot_time=snapshot,
            forecast_count=2,
        )


def test_late_price_run_does_not_use_previous_run() -> None:
    snapshot = datetime(2026, 8, 20, 10)
    previous = snapshot - timedelta(minutes=30)
    frame = pd.DataFrame(
        [price_row(previous, lead, 100, 1, sequence=1) for lead in range(48)]
        + [price_row(snapshot, lead, 200, 5, sequence=2) for lead in range(48)]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    with pytest.raises(RuntimeError, match="No price forecast or historical fallback"):
        service._build_run(
            data=service._clean_predispatch_prices(frame),
            location=BRISBANE,
            region="QLD1",
            snapshot_time=snapshot,
            forecast_count=48,
        )


@pytest.mark.parametrize("location_name", ["brisbane", "Murrumba Downs"])
def test_realised_prices_apply_tariffs_to_five_minute_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    location_name: str,
) -> None:
    start_utc = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    dispatch = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": datetime(2026, 8, 20, 4, 35 + 5 * index),
                "REGIONID": "QLD1",
                "INTERVENTION": 0,
                "RRP": 100.0 * (index + 1),
            }
            for index in range(3)
        ]
    )
    service = AemoPriceService(mock_archive_client(pd.DataFrame()), use_lightgbm=False)
    monkeypatch.setattr(
        service,
        "_compile_dispatch_prices",
        lambda *_: dispatch,
    )

    result = service.get_realised_prices(
        BRISBANE.model_copy(update={"location_name": location_name}),
        start_utc,
        start_utc + timedelta(minutes=15),
    )

    assert [row.timestamp_utc for row in result] == [
        start_utc + timedelta(minutes=5 * index) for index in range(3)
    ]
    assert [row.buy_tariff_aud for row in result] == [
        0.17695,
        0.27695,
        0.37695,
    ]
    assert [row.sell_tariff_aud for row in result] == [0.1, 0.2, 0.3]
    assert all(row.location_name == location_name for row in result)


@pytest.mark.parametrize(
    ("start_utc", "end_utc", "message"),
    [
        (
            datetime(2026, 8, 19, 18, 30),
            datetime(2026, 8, 19, 18, 35, tzinfo=UTC),
            "start_utc must be timezone-aware",
        ),
        (
            datetime(2026, 8, 19, 18, 30, tzinfo=UTC),
            datetime(2026, 8, 19, 18, 30, tzinfo=UTC),
            "end_utc must be after start_utc",
        ),
        (
            datetime(2026, 8, 19, 18, 31, tzinfo=UTC),
            datetime(2026, 8, 19, 18, 40, tzinfo=UTC),
            "start_utc must be on a 5-minute boundary",
        ),
    ],
)
def test_realised_prices_validate_window(
    start_utc: datetime, end_utc: datetime, message: str
) -> None:
    service = AemoPriceService(mock_archive_client(pd.DataFrame()), use_lightgbm=False)

    with pytest.raises(ValueError, match=message):
        service.get_realised_prices(BRISBANE, start_utc, end_utc)


def test_realised_prices_reject_incomplete_dispatch_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_utc = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    incomplete_dispatch = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": datetime(2026, 8, 20, 4, 35),
                "REGIONID": "QLD1",
                "INTERVENTION": 0,
                "RRP": 100.0,
            }
        ]
    )
    service = AemoPriceService(mock_archive_client(pd.DataFrame()), use_lightgbm=False)
    monkeypatch.setattr(
        service,
        "_compile_dispatch_prices",
        lambda *_: incomplete_dispatch,
    )

    with pytest.raises(RuntimeError, match="Expected 2 .* received 1"):
        service.get_realised_prices(
            BRISBANE, start_utc, start_utc + timedelta(minutes=10)
        )


def test_forecasts_reject_missing_first_period() -> None:
    snapshot = datetime(2026, 8, 20, 4, 30)
    frame = pd.DataFrame([price_row(snapshot, 1, 120, 1)])
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    with pytest.raises(RuntimeError, match="No price forecast or historical fallback"):
        service.get_price_forecasts(
            BRISBANE, snapshot.time(), snapshot.date(), horizon_period_count=2
        )


def test_price_forecast_loads_current_month_for_first_snapshot() -> None:
    frame = pd.DataFrame(
        columns=[
            "PREDISPATCHSEQNO",
            "RUNNO",
            "PERIODID",
            "REGIONID",
            "INTERVENTION",
            "DATETIME",
            "LASTCHANGED",
            "RRP",
        ]
    )
    client = mock_archive_client(frame)
    service = AemoPriceService(client, use_lightgbm=False)
    service._load_predispatch_window(
        first_snapshot=datetime(2026, 7, 1), last_snapshot=datetime(2026, 7, 1, 23, 30)
    )
    assert client.load_archive_table.call_args.kwargs["start_datetime"] == datetime(
        2026, 6, 24
    )


def test_predispatch_prices_reject_malformed_required_values() -> None:
    malformed = pd.DataFrame(
        [
            {
                "REGIONID": "QLD1",
                "INTERVENTION": 0,
                "DATETIME": "not-a-date",
                "LASTCHANGED": "2026-08-20 04:25:00",
                "RRP": "not-a-number",
            }
        ]
    )

    with pytest.raises(ValueError):
        AemoPriceService._clean_predispatch_prices(malformed)


def test_daily_forecasts_reuse_one_archive_with_complete_horizons() -> None:
    snapshot = datetime(2026, 8, 20, 4, 30)
    frame = pd.DataFrame(
        [
            price_row(
                snapshot + timedelta(minutes=30 * run),
                lead,
                100 + run + lead,
                1,
                sequence=run + 1,
            )
            for run in range(48)
            for lead in range(5)
        ]
    )
    client = mock_archive_client(frame)
    service = AemoPriceService(client, use_lightgbm=False)
    rows = service.get_price_forecasts(
        BRISBANE, time(4, 30), date(2026, 8, 20), horizon_period_count=5
    )
    assert len(rows) == 48
    assert len({row.run_time_utc for row in rows}) == 48
    assert rows[-1].sell_tariffs_aud == [0.147, 0.148, 0.149, 0.15, 0.151]
    assert all(len(row.target_times_utc) == 5 for row in rows)
    assert all(isinstance(row, BaseModel) for row in rows)
    assert rows[0].location_name == "brisbane"
    assert rows[0].run_time_utc == datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    assert rows[0].target_times_utc[0] == datetime(2026, 8, 19, 18, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        rows[0].buy_tariffs_aud = []
    client.load_archive_table.assert_called_once()


def test_default_forecast_horizon_is_48_for_every_run() -> None:
    snapshot = datetime(2026, 8, 20, 4, 30)
    frame = pd.DataFrame(
        [
            price_row(
                snapshot + timedelta(minutes=30 * run),
                lead,
                100 + lead,
                1,
                sequence=run + 1,
            )
            for run in range(48)
            for lead in range(48)
        ]
    )
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    rows = service.get_price_forecasts(BRISBANE, snapshot.time(), snapshot.date())
    assert len(rows) == 48
    assert [len(row.target_times_utc) for row in rows] == [48] * 48


@pytest.mark.parametrize(
    ("start_time", "message"),
    [
        (
            time(4, 30, tzinfo=UTC),
            "naive AEMO market time",
        ),
        (
            time(4, 15),
            "30-minute boundary",
        ),
    ],
)
def test_forecasts_validate_market_inputs(start_time: time, message: str) -> None:
    service = AemoPriceService(mock_archive_client(pd.DataFrame()), use_lightgbm=False)

    with pytest.raises(ValueError, match=message):
        service.get_price_forecasts(BRISBANE, start_time, date(2026, 8, 20))


@pytest.mark.parametrize("horizon_period_count", [0, -1])
def test_forecasts_reject_non_positive_horizon(horizon_period_count: int) -> None:
    service = AemoPriceService(mock_archive_client(pd.DataFrame()), use_lightgbm=False)

    with pytest.raises(ValueError, match="greater than zero"):
        service.get_price_forecasts(
            BRISBANE,
            time(4, 30),
            date(2026, 8, 20),
            horizon_period_count=horizon_period_count,
        )


@pytest.mark.parametrize("case", ["late_run", "missing_run", "late_target", "gap"])
def test_missing_prices_use_only_eligible_historical_slot_medians(case):
    snapshot = datetime(2026, 8, 20, 10)
    history = [
        price_row(
            snapshot - timedelta(days=day) + timedelta(minutes=30 * slot),
            0,
            price + slot * 100,
            1,
            sequence=day * 10 + slot,
        )
        for day, price in [(1, -100), (2, 300), (3, 100), (8, 9000)]
        for slot in range(3)
    ]
    # A later archive revision must not leak into the historical profile.
    revised = price_row(snapshot - timedelta(days=1), 0, 9999, 1, sequence=10)
    revised["LASTCHANGED"] = snapshot + timedelta(minutes=1)
    history.append(revised)
    current = (
        []
        if case == "missing_run"
        else [
            price_row(
                snapshot,
                lead,
                500 + lead * 100,
                5 if case == "late_run" or (case == "late_target" and lead == 1) else 1,
                sequence=999,
            )
            for lead in range(3)
            if not (case == "gap" and lead == 1)
        ]
    )
    frame = pd.DataFrame(history + current)
    service = AemoPriceService(mock_archive_client(frame), use_lightgbm=False)
    row = service._build_run(
        data=service._clean_predispatch_prices(frame),
        location=BRISBANE,
        region="QLD1",
        snapshot_time=snapshot,
        forecast_count=3,
    )
    expected = (
        [0.1, 0.2, 0.3] if case in ("late_run", "missing_run") else [0.5, 0.2, 0.7]
    )
    assert row.sell_tariffs_aud == expected
    assert row.forecast_sources == (
        ["historical"] * 3
        if case in ("late_run", "missing_run")
        else ["aemo", "historical", "aemo"]
    )
    assert len(row.target_times_utc) == 3
