"""Prepares regional solar, wind and demand forecast inputs for emissions prediction."""

import logging
from collections import OrderedDict
from zipfile import ZipFile

import numpy as np
import pandas as pd

from backend_v2.aemo.aemo_archive_client import AemoArchiveClient
from backend_v2.aemo.aemo_constants import (
    AEMO_PREDISPATCH_INTERVAL,
    FORECAST_PUBLICATION_GRACE,
    REGIONAL_FORECAST_REQUIRED,
    REGIONAL_FORECAST_VALUES,
    RENEWABLE_FEATURE_COLUMNS,
    AemoArchiveTable,
)

logger = logging.getLogger(__name__)


def load_generation_forecast_month(
    client: AemoArchiveClient, month: pd.Timestamp
) -> pd.DataFrame:
    """Read only necessary regional columns, allowing absent early renewable fields."""
    table = AemoArchiveTable.PREDISPATCHREGIONSUM
    archive_path = client.download_archive(table, month.to_pydatetime())
    with ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError("Expected one PREDISPATCHREGIONSUM CSV")
        with archive.open(names[0]) as file:
            columns = pd.read_csv(file, skiprows=1, nrows=0).columns
    missing_keys = set(REGIONAL_FORECAST_REQUIRED) - set(columns)
    if missing_keys:
        raise ValueError(f"Missing forecast keys in {month}: {missing_keys}")
    optional = [column for column in REGIONAL_FORECAST_VALUES if column in columns]
    raw = client.load_archive_table(
        table, month.to_pydatetime(), usecols=REGIONAL_FORECAST_REQUIRED + optional
    )
    for column in REGIONAL_FORECAST_VALUES:
        if column not in raw:
            logger.warning("%s has no %s; retaining NaN", month.date(), column)
            raw[column] = np.nan
    for column in ["RUNNO", "PERIODID", "INTERVENTION", *REGIONAL_FORECAST_VALUES]:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw = raw.loc[raw.INTERVENTION.eq(0)].copy()
    if raw[REGIONAL_FORECAST_REQUIRED].isna().any().any():
        raise ValueError(f"Missing regional forecast identifiers in {month}")
    for column in ["DATETIME", "LASTCHANGED"]:
        raw[column] = pd.to_datetime(raw[column], format="mixed", errors="raise")
    return raw[REGIONAL_FORECAST_REQUIRED + list(REGIONAL_FORECAST_VALUES)]


def generation_forecast_features(
    base: pd.DataFrame, forecasts: pd.DataFrame
) -> pd.DataFrame:
    """Select forecasts available at each decision time and derive renewable inputs."""
    raw = forecasts.copy()
    raw["target_time_market"] = (
        raw.DATETIME.astype("datetime64[ns]") - AEMO_PREDISPATCH_INTERVAL
    )
    raw["origin"] = raw.DATETIME.astype("datetime64[ns]") - pd.to_timedelta(
        raw.PERIODID.astype(int) * AEMO_PREDISPATCH_INTERVAL.total_seconds(), unit="s"
    )
    if not raw.origin.eq(raw.origin.dt.floor("30min")).all():
        raise ValueError("Regional forecast origins must be half-hour aligned")
    raw["available_at"] = pd.concat(
        [
            raw.origin + FORECAST_PUBLICATION_GRACE,
            raw.LASTCHANGED.astype("datetime64[ns]"),
        ],
        axis=1,
    ).max(axis=1)
    # Each event selects the newest published run, then its latest rerun/revision.
    # An old run updated late must not override an already available newer run.
    raw = raw.sort_values(["origin", "RUNNO", "LASTCHANGED"])
    raw["rank"] = np.arange(len(raw))
    raw = raw.sort_values(["available_at", "target_time_market", "rank"])
    best_rank = raw.groupby("target_time_market", sort=False)["rank"].cummax()
    raw = raw.loc[raw["rank"].eq(best_rank)].drop_duplicates(
        ["available_at", "target_time_market"], keep="last"
    )
    left = base[["decision_time_market", "target_time_market"]].copy()
    for column in left:
        left[column] = left[column].astype("datetime64[ns]")
    left["row_order"] = np.arange(len(left))
    raw = raw.rename(columns=REGIONAL_FORECAST_VALUES)
    joined = pd.merge_asof(
        left.sort_values("decision_time_market"),
        raw[
            [
                "available_at",
                "target_time_market",
                "origin",
                *REGIONAL_FORECAST_VALUES.values(),
            ]
        ].sort_values("available_at"),
        left_on="decision_time_market",
        right_on="available_at",
        by="target_time_market",
        direction="backward",
        allow_exact_matches=True,
    ).sort_values("row_order")
    solar, wind, demand = (joined[name] for name in REGIONAL_FORECAST_VALUES.values())
    renewables = solar + wind  # NaN if either input is absent.
    joined["forecast_residual_demand_mw"] = demand - renewables
    joined["forecast_renewable_share"] = renewables / demand.where(demand > 0)
    joined["generation_forecast_age_hours"] = (
        joined.decision_time_market - joined.origin
    ).dt.total_seconds() / 3600
    joined["generation_forecast_missing"] = (
        joined[list(REGIONAL_FORECAST_VALUES.values())].isna().any(axis=1).astype(int)
    )
    return joined[RENEWABLE_FEATURE_COLUMNS].astype("float32").reset_index(drop=True)


class AemoGenerationForecastService:
    """Reuse monthly source tables across daily emissions inference batches."""

    def __init__(self, client: AemoArchiveClient) -> None:
        """Set up archive access and a cache of monthly generation forecast tables."""
        self.client = client
        self._months: OrderedDict[str, pd.DataFrame] = OrderedDict()

    def features(self, base: pd.DataFrame, region: str) -> pd.DataFrame:
        # Archives contain forecasts issued in the month, including targets in
        # the next month. Future issue months cannot provide causal inputs.
        """Load regional generation forecasts and prepare inputs for emissions
        prediction.
        """
        first = (base.run_time_market.min() - pd.Timedelta(days=2)).to_period("M")
        last = base.run_time_market.max().to_period("M")
        parts = []
        for month in pd.date_range(first.start_time, last.start_time, freq="MS"):
            key = month.strftime("%Y-%m")
            if key not in self._months:
                self._months[key] = load_generation_forecast_month(self.client, month)
            self._months.move_to_end(key)
            parts.append(
                self._months[key].loc[self._months[key].REGIONID.eq(region)].copy()
            )
            while len(self._months) > 2:
                self._months.popitem(last=False)
        return generation_forecast_features(base, pd.concat(parts, ignore_index=True))
