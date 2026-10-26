"""Provides historical and forecast electricity prices with network tariffs applied.

All datetimes used by this module are naive AEMO market times
(``Australia/Brisbane``). Conversion to and from UTC belongs at the pipeline
boundary, where a location's timezone is available.
"""

from datetime import date, datetime, time, timedelta
from typing import SupportsFloat, cast

import pandas as pd

from backend_v2.aemo.aemo_archive_client import (
    AemoArchiveClient,
    market_time_to_utc,
    normalise_region,
    utc_to_market_time,
)
from backend_v2.aemo.aemo_constants import (
    AEMO_DISPATCH_INTERVAL,
    AEMO_PREDISPATCH_INTERVAL,
    AEMO_PREDISPATCH_PERIODS_PER_DAY,
    FORECAST_HISTORY_DAYS,
    FORECAST_PUBLICATION_GRACE,
    AemoArchiveTable,
)
from backend_v2.aemo.aemo_forecast_service import AemoForecastService
from backend_v2.aemo.tariff_service import TariffService
from database_v2.models.data_models import (
    ForecastPriceSnapshot,
    PriceForecastSource,
    RealisedPriceRow,
)
from database_v2.models.request_models import Location

PREDISPATCH_COLUMNS = [
    "PREDISPATCHSEQNO",
    "RUNNO",
    "REGIONID",
    "PERIODID",
    "INTERVENTION",
    "DATETIME",
    "LASTCHANGED",
    "RRP",
]


def _validate_half_hour(value: datetime | time, name: str) -> None:
    """Require a naive time aligned to the predispatch interval."""
    if value.tzinfo is not None:
        raise ValueError(f"{name} must use naive AEMO market time.")
    interval_minutes = int(AEMO_PREDISPATCH_INTERVAL.total_seconds() // 60)
    if value.minute % interval_minutes or value.second or value.microsecond:
        raise ValueError(f"{name} must be on a {interval_minutes}-minute boundary.")


class AemoPriceService:
    """Fetch realised and forecast AEMO prices with network tariffs applied."""

    def __init__(
        self,
        client: AemoArchiveClient | None = None,
        tariff_service: TariffService | None = None,
        *,
        use_lightgbm: bool = True,
        forecast_service: AemoForecastService | None = None,
    ) -> None:
        """Create the service with injectable archive and tariff dependencies."""
        self.client = client or AemoArchiveClient()
        self.tariff_service = tariff_service or TariffService()
        self.use_lightgbm = use_lightgbm
        self.forecast_service = forecast_service or AemoForecastService(self.client)

    def _prices_with_tariffs(
        self,
        location: Location,
        timestamp_utc: datetime,
        spot_aud_per_mwh: float,
    ) -> tuple[float, float]:
        """Convert spot AUD/MWh and add applicable network tariff rates."""
        spot_aud_per_kwh = spot_aud_per_mwh / 1000
        network_rates = self.tariff_service.rates_for(
            location.location_name, timestamp_utc, nem_region=location.nem_region
        )
        return (
            round(spot_aud_per_kwh + network_rates.import_aud_per_kwh, 6),
            round(spot_aud_per_kwh + network_rates.export_aud_per_kwh, 6),
        )

    def _compile_dispatch_prices(
        self, region: str, start_market: datetime, end_market: datetime
    ) -> pd.DataFrame:
        """Load regional DISPATCHPRICE rows from the monthly AEMO archive."""
        data = self.client.load_archive_table(
            table=AemoArchiveTable.DISPATCHPRICE,
            start_datetime=start_market,
            end_datetime=end_market,
            usecols=["SETTLEMENTDATE", "REGIONID", "RRP", "INTERVENTION"],
        )
        return data[(data["REGIONID"] == region) & (data["INTERVENTION"] == 0)]

    def get_realised_prices(
        self,
        location: Location,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[RealisedPriceRow]:
        """Return tariff-adjusted realised prices for an exact five-minute window."""
        start_market = utc_to_market_time(start_utc, "start_utc")
        end_market = utc_to_market_time(end_utc, "end_utc")
        if end_utc <= start_utc:
            raise ValueError("end_utc must be after start_utc.")
        interval_minutes = int(AEMO_DISPATCH_INTERVAL.total_seconds() // 60)
        for value, name in ((start_market, "start_utc"), (end_market, "end_utc")):
            if value.minute % interval_minutes or value.second or value.microsecond:
                raise ValueError(
                    f"{name} must be on a {interval_minutes}-minute boundary."
                )

        data = self._compile_dispatch_prices(
            normalise_region(location.nem_region), start_market, end_market
        ).copy()
        data["SETTLEMENTDATE"] = pd.to_datetime(data["SETTLEMENTDATE"], errors="raise")
        data["RRP"] = pd.to_numeric(data["RRP"], errors="raise")
        required = ["SETTLEMENTDATE", "REGIONID", "RRP"]
        missing = [column for column in required if data[column].isna().any()]
        if missing:
            raise RuntimeError(
                "DISPATCHPRICE contains missing required values in: "
                + ", ".join(missing)
            )
        # SETTLEMENTDATE marks the interval end; database rows use interval starts.
        data["INTERVAL_START"] = data["SETTLEMENTDATE"] - AEMO_DISPATCH_INTERVAL
        data = data[
            (data["INTERVAL_START"] >= pd.Timestamp(start_market))
            & (data["INTERVAL_START"] < pd.Timestamp(end_market))
        ]
        data = data.sort_values("SETTLEMENTDATE").drop_duplicates(
            subset=["INTERVAL_START", "REGIONID"], keep="last"
        )

        expected_count = int((end_utc - start_utc) / AEMO_DISPATCH_INTERVAL)
        if len(data) != expected_count:
            raise RuntimeError(
                f"Expected {expected_count} DISPATCHPRICE rows, received {len(data)}."
            )

        realised: list[RealisedPriceRow] = []
        for row in data.itertuples():
            timestamp_utc = market_time_to_utc(
                cast(pd.Timestamp, row.INTERVAL_START).to_pydatetime()
            )
            buy_price, sell_price = self._prices_with_tariffs(
                location, timestamp_utc, float(cast(SupportsFloat, row.RRP))
            )
            realised.append(
                RealisedPriceRow(
                    timestamp_utc=timestamp_utc,
                    location_name=location.location_name,
                    buy_tariff_aud=buy_price,
                    sell_tariff_aud=sell_price,
                )
            )
        return realised

    @staticmethod
    def _clean_predispatch_prices(data: pd.DataFrame) -> pd.DataFrame:
        """Parse and validate the columns specific to PREDISPATCHPRICE."""
        data = data.copy()
        data["DATETIME"] = pd.to_datetime(data["DATETIME"], errors="raise")
        data["LASTCHANGED"] = pd.to_datetime(data["LASTCHANGED"], errors="raise")
        data["RUNNO"] = pd.to_numeric(data["RUNNO"], errors="raise")
        data["PERIODID"] = pd.to_numeric(data["PERIODID"], errors="raise")
        data["INTERVENTION"] = pd.to_numeric(data["INTERVENTION"], errors="raise")
        data["RRP"] = pd.to_numeric(data["RRP"], errors="raise")
        required = PREDISPATCH_COLUMNS
        missing = [column for column in required if data[column].isna().any()]
        if missing:
            raise RuntimeError(
                "PREDISPATCHPRICE contains missing required values in: "
                + ", ".join(missing)
            )
        return data

    def _load_predispatch_window(
        self, *, first_snapshot: datetime, last_snapshot: datetime
    ) -> pd.DataFrame:
        """Load requested runs and the historical window for missing targets."""
        data = self.client.load_archive_table(
            table=AemoArchiveTable.PREDISPATCHPRICE,
            start_datetime=first_snapshot - timedelta(days=FORECAST_HISTORY_DAYS),
            end_datetime=last_snapshot,
            usecols=PREDISPATCH_COLUMNS,
        )
        return self._clean_predispatch_prices(data)

    def _build_spot_run(
        self,
        *,
        data: pd.DataFrame,
        region: str,
        snapshot_time: datetime,
        forecast_count: int,
    ) -> tuple[list[float], list[PriceForecastSource]]:
        """Build a wholesale price forecast, filling missing targets with historical
        medians.
        """
        horizon_end = snapshot_time + AEMO_PREDISPATCH_INTERVAL * forecast_count
        available_at = snapshot_time + FORECAST_PUBLICATION_GRACE
        first_interval_end = snapshot_time + AEMO_PREDISPATCH_INTERVAL
        anchors = data[
            (data["REGIONID"] == region)
            & (data["INTERVENTION"] == 0)
            & (data["PERIODID"] == 1)
            & (data["DATETIME"] == pd.Timestamp(first_interval_end))
            & (data["LASTCHANGED"] <= pd.Timestamp(available_at))
        ].sort_values(["LASTCHANGED", "RUNNO"])
        available = data.iloc[:0]
        if not anchors.empty:
            run = anchors.iloc[-1]
            available = data[
                (data["PREDISPATCHSEQNO"] == run["PREDISPATCHSEQNO"])
                & (data["RUNNO"] == run["RUNNO"])
                & (data["REGIONID"] == region)
                & (data["INTERVENTION"] == 0)
                & (data["DATETIME"] > pd.Timestamp(snapshot_time))
                & (data["DATETIME"] <= pd.Timestamp(horizon_end))
                & (data["LASTCHANGED"] <= pd.Timestamp(available_at))
            ]
        # Keep the latest eligible revision within this run for each target.
        available = (
            available.sort_values(["DATETIME", "LASTCHANGED"])
            .drop_duplicates(subset=["DATETIME"], keep="last")
            .sort_values("DATETIME")
        )
        # PREDISPATCH DATETIME denotes interval end, while targets use starts.
        aemo_forecasts = {
            cast(pd.Timestamp, row.DATETIME) - AEMO_PREDISPATCH_INTERVAL: round(
                float(cast(SupportsFloat, row.RRP)), 2
            )
            for row in available.itertuples()
        }
        # Historical first-period prices are prior short-horizon forecasts,
        # not realised prices. Only revisions known at this run are eligible.
        history = (
            data[
                (data["REGIONID"] == region)
                & (data["INTERVENTION"] == 0)
                & (data["PERIODID"] == 1)
                & (
                    data["DATETIME"]
                    > snapshot_time - timedelta(days=FORECAST_HISTORY_DAYS)
                )
                & (data["DATETIME"] <= snapshot_time)
                & (data["LASTCHANGED"] <= snapshot_time)
            ]
            .sort_values(["LASTCHANGED", "RUNNO"])
            .drop_duplicates("DATETIME", keep="last")
        )
        history = history.copy()
        starts = history["DATETIME"] - AEMO_PREDISPATCH_INTERVAL
        history["slot"] = starts.dt.hour * 2 + starts.dt.minute // 30
        profile = history.groupby("slot")["RRP"].median()

        spot_prices: list[float] = []
        forecast_sources: list[PriceForecastSource] = []
        for lead in range(forecast_count):
            target_time = snapshot_time + AEMO_PREDISPATCH_INTERVAL * lead
            price = aemo_forecasts.get(pd.Timestamp(target_time))
            source: PriceForecastSource = "aemo"
            if price is None:
                source = "historical"
                slot = target_time.hour * 2 + target_time.minute // 30
                if slot not in profile:
                    raise RuntimeError(
                        "No price forecast or historical fallback for run "
                        f"{snapshot_time.isoformat()}, target "
                        f"{target_time.isoformat()} "
                        f"using the previous {FORECAST_HISTORY_DAYS} days."
                    )
                price = float(profile[slot])
            forecast_sources.append(source)
            spot_prices.append(price)
        return spot_prices, forecast_sources

    def _build_run(
        self,
        *,
        data: pd.DataFrame,
        location: Location,
        region: str,
        snapshot_time: datetime,
        forecast_count: int,
    ) -> ForecastPriceSnapshot:
        """Build a baseline price forecast snapshot with network tariffs applied."""
        prices, forecast_sources = self._build_spot_run(
            data=data,
            region=region,
            snapshot_time=snapshot_time,
            forecast_count=forecast_count,
        )
        target_times_utc = [
            market_time_to_utc(snapshot_time + AEMO_PREDISPATCH_INTERVAL * lead)
            for lead in range(forecast_count)
        ]
        tariffs = [
            self._prices_with_tariffs(location, target, price)
            for target, price in zip(target_times_utc, prices, strict=True)
        ]
        return ForecastPriceSnapshot(
            run_time_utc=market_time_to_utc(snapshot_time),
            location_name=location.location_name,
            target_times_utc=target_times_utc,
            buy_tariffs_aud=[buy for buy, _ in tariffs],
            sell_tariffs_aud=[sell for _, sell in tariffs],
            forecast_sources=forecast_sources,
        )

    def get_price_forecasts(
        self,
        location: Location,
        start_time: time,
        day: date,
        horizon_period_count: int = AEMO_PREDISPATCH_PERIODS_PER_DAY,
    ) -> list[ForecastPriceSnapshot]:
        """Build 48 rolling runs with model-corrected prices and normal tariffs."""
        region = normalise_region(location.nem_region)
        _validate_half_hour(start_time, "start_time")
        if horizon_period_count <= 0:
            raise ValueError("horizon_period_count must be greater than zero.")

        start_datetime = datetime.combine(day, start_time)
        last_snapshot = start_datetime + AEMO_PREDISPATCH_INTERVAL * (
            AEMO_PREDISPATCH_PERIODS_PER_DAY - 1
        )
        data = self._load_predispatch_window(
            first_snapshot=start_datetime,
            last_snapshot=last_snapshot,
        )

        snapshots: list[ForecastPriceSnapshot] = []
        for run_index in range(AEMO_PREDISPATCH_PERIODS_PER_DAY):
            snapshots.append(
                self._build_run(
                    data=data,
                    location=location,
                    region=region,
                    snapshot_time=(
                        start_datetime + AEMO_PREDISPATCH_INTERVAL * run_index
                    ),
                    forecast_count=horizon_period_count,
                )
            )
        if self.use_lightgbm:
            forecasts = self.forecast_service.predict(
                location=location,
                start_market=start_datetime,
                end_market=last_snapshot + AEMO_PREDISPATCH_INTERVAL,
                horizon_period_count=horizon_period_count,
                kind="price",
                predispatch=data,
            )
            corrected = []
            for snapshot in snapshots:
                run = utc_to_market_time(snapshot.run_time_utc)
                tariffs = [
                    self._prices_with_tariffs(location, target, float(price))
                    for target, price in zip(
                        snapshot.target_times_utc, forecasts[run], strict=True
                    )
                ]
                # Sources retain the AEMO/historical origin of the baseline.
                corrected.append(
                    snapshot.model_copy(
                        update={
                            "buy_tariffs_aud": [buy for buy, _ in tariffs],
                            "sell_tariffs_aud": [sell for _, sell in tariffs],
                        }
                    )
                )
            return corrected
        return snapshots
