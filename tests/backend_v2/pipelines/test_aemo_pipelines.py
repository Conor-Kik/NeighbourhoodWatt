"""Exercise both AEMO pipelines through the v2 runner and SQLite DAOs."""

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from team_21_the_grid_optimists import cli as cli_module

from backend_v2.aemo.aemo_archive_client import market_time_to_utc
from backend_v2.aemo.aemo_emissions_service import AemoEmissionsService
from backend_v2.aemo.aemo_price_service import AemoPriceService
from backend_v2.pipeline_runner import ensure_data
from backend_v2.pipelines.emissions_pipeline import EmissionsPipeline
from backend_v2.pipelines.price_pipeline import PricePipeline
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.price_dao import PriceDao
from database_v2.db_initilisation import reset_db
from database_v2.models.data_models import (
    ForecastEmissionsSnapshot,
    ForecastPriceSnapshot,
    RealisedEmissionsRow,
    RealisedPriceRow,
)


def test_aemo_cli_runs_each_day_separately(tmp_path, monkeypatch):
    db_path = tmp_path / "daily.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    windows = []
    monkeypatch.setattr(
        cli_module,
        "ensure_data",
        lambda request, path, pipelines: windows.append(
            (request.start_time_utc, request.end_time_utc, path)
        ),
    )
    start = datetime(2026, 7, 1, tzinfo=UTC)

    cli_module.run_aemo_pipelines(location="Brisbane", start=start, days=2)

    assert windows == [
        (start, start + timedelta(days=1), db_path),
        (start + timedelta(days=1), start + timedelta(days=2), db_path),
    ]


def test_aemo_runner_persists_and_skips_complete_window(tmp_path, monkeypatch):
    db_path = tmp_path / "aemo.db"
    reset_db(db_path)
    location = LocationDao(str(db_path)).get_location("Brisbane")
    assert location is not None
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    request = SimpleNamespace(
        start_time_utc=start,
        end_time_utc=end,
        location_name="Brisbane",
    )
    calls = {
        "realised_emissions": 0,
        "forecast_emissions": 0,
        "realised_prices": 0,
        "forecast_prices": 0,
    }

    def realised_emissions(self, location, start_utc, end_utc):
        calls["realised_emissions"] += 1
        return [
            RealisedEmissionsRow(
                timestamp_utc=start + timedelta(minutes=5 * i),
                location_name=location.location_name,
                carbon_intensity_t_per_mwh=0.5,
            )
            for i in range(12)
        ]

    def forecast_emissions(self, location, start_utc, end_utc):
        assert self.use_lightgbm
        calls["forecast_emissions"] += 1
        return [
            ForecastEmissionsSnapshot(
                run_time_utc=start + timedelta(minutes=30 * i),
                location_name=location.location_name,
                target_times_utc=[
                    start + timedelta(minutes=30 * (i + j)) for j in range(48)
                ],
                carbon_intensities_t_per_mwh=[0.5] * 48,
            )
            for i in range(2)
        ]

    def realised_prices(self, location, start_utc, end_utc):
        calls["realised_prices"] += 1
        return [
            RealisedPriceRow(
                timestamp_utc=start + timedelta(minutes=5 * i),
                location_name=location.location_name,
                buy_tariff_aud=0.3,
                sell_tariff_aud=0.1,
            )
            for i in range(12)
        ]

    def forecast_prices(self, location, start_time, day):
        assert self.use_lightgbm
        calls["forecast_prices"] += 1
        return [
            ForecastPriceSnapshot(
                run_time_utc=start + timedelta(minutes=30 * i),
                location_name=location.location_name,
                target_times_utc=[
                    start + timedelta(minutes=30 * (i + j)) for j in range(48)
                ],
                buy_tariffs_aud=[0.3] * 48,
                sell_tariffs_aud=[0.1] * 48,
                forecast_sources=["aemo"] * 48,
            )
            for i in range(2)
        ]

    monkeypatch.setattr(
        AemoEmissionsService, "calculate_realised_emissions", realised_emissions
    )
    monkeypatch.setattr(
        AemoEmissionsService, "calculate_forecast_emissions", forecast_emissions
    )
    monkeypatch.setattr(AemoPriceService, "get_realised_prices", realised_prices)
    monkeypatch.setattr(AemoPriceService, "get_price_forecasts", forecast_prices)

    ensure_data(request, db_path, pipelines=(EmissionsPipeline(), PricePipeline()))
    ensure_data(request, db_path, pipelines=(EmissionsPipeline(), PricePipeline()))

    assert calls == {key: 1 for key in calls}, calls
    with sqlite3.connect(db_path) as conn:
        for table, expected in (
            ("realised_emissions_5m", 12),
            ("forecast_emissions_30m", 96),
            ("realised_pricing_5m", 12),
            ("forecast_pricing_30m", 96),
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == expected


def test_price_pipeline_loads_forecasts_for_more_than_one_day(tmp_path, monkeypatch):
    db_path = tmp_path / "multi_day.db"
    reset_db(db_path)
    location = LocationDao(str(db_path)).get_location("Brisbane")
    assert location is not None
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = start + timedelta(hours=50)
    request = SimpleNamespace(
        start_time_utc=start,
        end_time_utc=end,
        location_name="Brisbane",
    )
    price_dao = PriceDao(db_path)
    price_dao.save_realised_prices(
        [
            RealisedPriceRow(
                timestamp_utc=start + timedelta(minutes=5 * index),
                location_name=location.location_name,
                buy_tariff_aud=0.3,
                sell_tariff_aud=0.1,
            )
            for index in range(50 * 12)
        ]
    )
    price_dao.close()
    calls = []

    def forecast_prices(self, location, start_time, day):
        run_start = market_time_to_utc(datetime.combine(day, start_time))
        calls.append(run_start)
        return [
            ForecastPriceSnapshot(
                run_time_utc=run_start + timedelta(minutes=30 * index),
                location_name=location.location_name,
                target_times_utc=[
                    run_start + timedelta(minutes=30 * (index + lead))
                    for lead in range(48)
                ],
                buy_tariffs_aud=[0.3] * 48,
                sell_tariffs_aud=[0.1] * 48,
                forecast_sources=["aemo"] * 48,
            )
            for index in range(48)
        ]

    monkeypatch.setattr(AemoPriceService, "get_price_forecasts", forecast_prices)
    PricePipeline().ensure_data(request, db_path)

    assert calls == [start, start + timedelta(days=1), start + timedelta(days=2)]
    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM forecast_pricing_30m").fetchone()[0]
            == 100 * 48
        )
