"""Calculates historical grid emissions per unit of electricity and forecasts them.

All AEMO data is processed in naive market time (Australia/Brisbane). Public
service inputs and database rows use timezone-aware UTC datetimes.

Forecast emissions start with a historical half-hour median profile built from
realised DISPATCH data. DISPATCHLOAD is published the next day; a two-day
input delay is a conservative margin on that publication lag. These modeled
values are not AEMO-issued forecasts. Saved LightGBM residual corrections are
applied by default; baseline-only mode is available for training and comparison.
"""

from datetime import datetime, timedelta
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
    EMISSIONS_HISTORY_DELAY,
    FEATURE_HISTORY_DAYS,
    FORECAST_HISTORY_DAYS,
    FORECAST_PUBLICATION_GRACE,
    AemoArchiveTable,
)
from backend_v2.aemo.aemo_forecast_features import (
    baseline_emissions,
    emissions_profile_history,
)
from backend_v2.aemo.aemo_forecast_service import AemoForecastService
from database_v2.models.data_models import (
    ForecastEmissionsSnapshot,
    RealisedEmissionsRow,
)
from database_v2.models.request_models import Location


class IncompleteEmissionsDataError(RuntimeError):
    """The requested dates lack complete realised emissions data."""


def _validate_boundary(value: datetime, name: str, interval: timedelta) -> None:
    """Require a timestamp to align exactly with an AEMO market interval."""
    minutes = int(interval.total_seconds() // 60)
    if value.minute % minutes or value.second or value.microsecond:
        raise ValueError(f"{name} must be on a {minutes}-minute boundary.")


class AemoEmissionsService:
    """Fetch and calculate region-level AEMO emissions intensity."""

    def __init__(
        self,
        client: AemoArchiveClient | None = None,
        *,
        use_lightgbm: bool = True,
        forecast_service: AemoForecastService | None = None,
    ) -> None:
        """Create the service with an injectable archive client for testing."""
        self.client = client or AemoArchiveClient()
        self.use_lightgbm = use_lightgbm
        self.forecast_service = forecast_service or AemoForecastService(self.client)

    def _get_generator_emissions_factors(
        self, run_datetime: datetime
    ) -> dict[str, float]:
        """Look up emissions per unit of electricity for each generator identifier."""
        genunits = self.client.load_archive_table(
            table=AemoArchiveTable.GENUNITS,
            start_datetime=run_datetime,
            usecols=["GENSETID", "CO2E_EMISSIONS_FACTOR"],
        )
        dualloc = self.client.load_archive_table(
            table=AemoArchiveTable.DUALLOC,
            start_datetime=run_datetime,
            usecols=["GENSETID", "DUID"],
        )

        # Dispatch data uses DUIDs, while emissions factors are keyed by GENSETID.
        merged = dualloc.merge(genunits, on="GENSETID", how="left").drop_duplicates(
            "DUID"
        )
        merged["CO2E_EMISSIONS_FACTOR"] = pd.to_numeric(
            merged["CO2E_EMISSIONS_FACTOR"], errors="raise"
        )
        available = merged.dropna(subset=["DUID", "CO2E_EMISSIONS_FACTOR"])
        return dict(
            zip(
                available["DUID"].astype(str),
                available["CO2E_EMISSIONS_FACTOR"].astype(float),
                strict=False,
            )
        )

    def _get_duid_region_map(self, run_datetime: datetime) -> dict[str, str]:
        """Look up the electricity market region for each generator identifier."""
        dudetail = self.client.load_archive_table(
            table=AemoArchiveTable.DUDETAILSUMMARY,
            start_datetime=run_datetime,
            usecols=["DUID", "REGIONID"],
        )
        return dict(
            zip(
                dudetail["DUID"].astype(str),
                dudetail["REGIONID"].astype(str),
                strict=False,
            )
        )

    def _compile_dispatchload(
        self, start_aemo: datetime, end_aemo: datetime, duids: list[str]
    ) -> pd.DataFrame:
        """Load historical generator output records through the archive client."""
        return self.client.load_interval_table(
            table_name="DISPATCHLOAD",
            start_market=start_aemo,
            end_market=end_aemo,
            select_columns=[
                "SETTLEMENTDATE",
                "DUID",
                "INTERVENTION",
                "TOTALCLEARED",
            ],
            filter_cols=["DUID", "INTERVENTION"],
            filter_values=(duids, [0]),
        )

    def _load_realised_emissions(
        self,
        *,
        region: str,
        start_market: datetime,
        end_market: datetime,
    ) -> pd.DataFrame:
        """Calculate region totals and intensity for each dispatch interval."""
        factors = self._get_generator_emissions_factors(start_market)
        duid_regions = self._get_duid_region_map(start_market)
        duids = [
            duid for duid, duid_region in duid_regions.items() if duid_region == region
        ]
        if not duids:
            raise RuntimeError(f"No DUIDs found for region {region}.")

        dispatch = self._compile_dispatchload(start_market, end_market, duids).copy()
        if dispatch.empty:
            raise RuntimeError(
                f"No DISPATCHLOAD records returned for {region} from "
                f"{start_market} to {end_market}."
            )

        dispatch["SETTLEMENTDATE"] = pd.to_datetime(
            dispatch["SETTLEMENTDATE"], errors="raise"
        )
        dispatch["TOTALCLEARED"] = pd.to_numeric(
            dispatch["TOTALCLEARED"], errors="raise"
        )

        required = ["SETTLEMENTDATE", "DUID", "TOTALCLEARED"]
        missing = [column for column in required if dispatch[column].isna().any()]
        if missing:
            raise RuntimeError(
                "DISPATCHLOAD contains missing required values in: "
                + ", ".join(missing)
            )

        # SETTLEMENTDATE marks the interval end; database rows use interval starts.
        dispatch["INTERVAL_START"] = dispatch["SETTLEMENTDATE"] - AEMO_DISPATCH_INTERVAL
        dispatch = dispatch[
            (dispatch["INTERVAL_START"] >= pd.Timestamp(start_market))
            & (dispatch["INTERVAL_START"] < pd.Timestamp(end_market))
        ]
        dispatch = dispatch.sort_values("SETTLEMENTDATE").drop_duplicates(
            subset=["INTERVAL_START", "DUID"], keep="last"
        )

        dispatch["CO2E_EMISSIONS_FACTOR"] = dispatch["DUID"].astype(str).map(factors)
        # AEMO's CDEII method excludes generation without a published factor.
        dispatch = dispatch.dropna(subset=["CO2E_EMISSIONS_FACTOR"])
        if dispatch.empty:
            raise RuntimeError(f"No emissions factors found for region {region}.")

        # TOTALCLEARED is MW, so multiply by interval hours to obtain MWh.
        dispatch["generation_mwh"] = dispatch["TOTALCLEARED"].clip(lower=0) * (
            AEMO_DISPATCH_INTERVAL.total_seconds() / 3600
        )
        dispatch["emissions_tco2e"] = (
            dispatch["generation_mwh"] * dispatch["CO2E_EMISSIONS_FACTOR"]
        )

        by_interval = (
            dispatch.groupby("INTERVAL_START")
            .agg(
                emissions_tco2e=("emissions_tco2e", "sum"),
                generation_mwh=("generation_mwh", "sum"),
            )
            .reset_index()
            .sort_values("INTERVAL_START")
        )

        # Regional intensity is the generation-weighted average factor (t/MWh).
        by_interval["intensity_t_per_mwh"] = (
            by_interval["emissions_tco2e"] / by_interval["generation_mwh"]
        ).replace([float("inf"), -float("inf")], float("nan"))
        return by_interval.dropna(subset=["intensity_t_per_mwh"])

    def calculate_realised_emissions(
        self, location: Location, start_utc: datetime, end_utc: datetime
    ) -> list[RealisedEmissionsRow]:
        """Return region-level emissions for an exact five-minute UTC window."""
        start_market = utc_to_market_time(start_utc, "start_utc")
        end_market = utc_to_market_time(end_utc, "end_utc")
        if end_utc <= start_utc:
            raise ValueError("end_utc must be after start_utc.")
        _validate_boundary(start_market, "start_utc", AEMO_DISPATCH_INTERVAL)
        _validate_boundary(end_market, "end_utc", AEMO_DISPATCH_INTERVAL)

        data = self._load_realised_emissions(
            region=normalise_region(location.nem_region),
            start_market=start_market,
            end_market=end_market,
        )
        expected_count = int((end_utc - start_utc) / AEMO_DISPATCH_INTERVAL)
        if len(data) != expected_count:
            expected = pd.date_range(
                start_market, end_market, freq=AEMO_DISPATCH_INTERVAL, inclusive="left"
            )
            missing = expected.difference(pd.DatetimeIndex(data["INTERVAL_START"]))
            dates = ", ".join(missing.strftime("%Y-%m-%d").unique())
            raise IncompleteEmissionsDataError(
                f"Cannot use this date range for {location.location_name}: "
                f"AEMO emissions data is incomplete on {dates} (AEMO market time). "
                "Choose a date range that avoids these dates."
            )

        return [
            RealisedEmissionsRow(
                timestamp_utc=market_time_to_utc(
                    cast(pd.Timestamp, row.INTERVAL_START).to_pydatetime()
                ),
                location_name=location.location_name,
                carbon_intensity_t_per_mwh=round(
                    float(cast(SupportsFloat, row.intensity_t_per_mwh)), 4
                ),
            )
            for row in data.itertuples()
        ]

    def calculate_forecast_emissions(
        self,
        location: Location,
        start_utc: datetime,
        end_utc: datetime,
        horizon_period_count: int = AEMO_PREDISPATCH_PERIODS_PER_DAY,
    ) -> list[ForecastEmissionsSnapshot]:
        """Apply saved model corrections to the historical half-hour baseline.

        Use realised DISPATCH intervals at least two days old. This gives a
        simple daily profile without treating same-day dispatch data as a
        live AEMO forecast. Require history for every requested time-of-day slot.
        Baseline-only mode preserves the uncorrected profile and output schema.
        """
        start_market = utc_to_market_time(start_utc, "start_utc")
        end_market = utc_to_market_time(end_utc, "end_utc")
        if end_utc <= start_utc:
            raise ValueError("end_utc must be after start_utc.")
        if horizon_period_count <= 0:
            raise ValueError("horizon_period_count must be greater than zero.")
        _validate_boundary(start_market, "start_utc", AEMO_PREDISPATCH_INTERVAL)
        _validate_boundary(end_market, "end_utc", AEMO_PREDISPATCH_INTERVAL)
        period_count = int((end_utc - start_utc) / AEMO_PREDISPATCH_INTERVAL)
        last_snapshot = end_market - AEMO_PREDISPATCH_INTERVAL
        history_start = (
            start_market
            - EMISSIONS_HISTORY_DELAY
            - timedelta(days=FORECAST_HISTORY_DAYS)
        )
        history_end = last_snapshot - EMISSIONS_HISTORY_DELAY
        if self.use_lightgbm:
            history_start = start_market - timedelta(days=FEATURE_HISTORY_DAYS)
            history_end += FORECAST_PUBLICATION_GRACE

        dispatch = self._load_realised_emissions(
            region=normalise_region(location.nem_region),
            start_market=history_start,
            end_market=history_end,
        )
        history = emissions_profile_history(dispatch)
        snapshots: list[ForecastEmissionsSnapshot] = []
        for index in range(period_count):
            snapshot = start_market + index * AEMO_PREDISPATCH_INTERVAL
            targets = [
                snapshot + lead * AEMO_PREDISPATCH_INTERVAL
                for lead in range(horizon_period_count)
            ]
            snapshots.append(
                ForecastEmissionsSnapshot(
                    run_time_utc=market_time_to_utc(snapshot),
                    location_name=location.location_name,
                    target_times_utc=[market_time_to_utc(target) for target in targets],
                    carbon_intensities_t_per_mwh=baseline_emissions(
                        history, snapshot, pd.DatetimeIndex(targets)
                    ),
                )
            )
        if self.use_lightgbm:
            forecasts = self.forecast_service.predict(
                location=location,
                start_market=start_market,
                end_market=end_market,
                horizon_period_count=horizon_period_count,
                kind="emissions",
                dispatch=dispatch,
            )
            return [
                snapshot.model_copy(
                    update={
                        "carbon_intensities_t_per_mwh": [
                            round(float(value), 4)
                            for value in forecasts[
                                utc_to_market_time(snapshot.run_time_utc)
                            ]
                        ],
                    }
                )
                for snapshot in snapshots
            ]
        return snapshots
