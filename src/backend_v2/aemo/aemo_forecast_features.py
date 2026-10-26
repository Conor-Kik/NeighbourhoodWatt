"""Prepares forecast model inputs using information available at the simulated time."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend_v2.aemo.aemo_constants import (
    AEMO_DISPATCH_INTERVAL,
    AEMO_PREDISPATCH_INTERVAL,
    EMISSIONS_HISTORY_DELAY,
    FEATURE_COLUMNS,
    FORECAST_HISTORY_DAYS,
    FORECAST_PUBLICATION_GRACE,
    PRICE_PUBLICATION_DELAY,
)
from database_v2.models.data_models import PriceForecastSource


def half_hour_history(
    prices: pd.DataFrame,
    dispatch: pd.DataFrame,
) -> tuple[pd.Series, pd.Series]:
    """Average six complete five-minute intervals, matching the trained signals."""
    prices = prices.copy()
    prices["SETTLEMENTDATE"] = pd.to_datetime(
        prices["SETTLEMENTDATE"], format="mixed", errors="raise"
    )
    prices["INTERVAL_START"] = prices["SETTLEMENTDATE"] - AEMO_DISPATCH_INTERVAL
    prices["RRP"] = pd.to_numeric(prices["RRP"], errors="raise")
    prices = prices.sort_values("SETTLEMENTDATE").drop_duplicates(
        "INTERVAL_START", keep="last"
    )
    dispatch = dispatch.copy()
    dispatch["INTERVAL_START"] = pd.to_datetime(
        dispatch["INTERVAL_START"], format="mixed", errors="raise"
    )
    dispatch = dispatch.sort_values("INTERVAL_START").drop_duplicates(
        "INTERVAL_START", keep="last"
    )

    def aggregate(frame: pd.DataFrame, column: str) -> pd.Series:
        """Average each complete group of six five-minute readings into one half-hour
        value.
        """
        grouped = frame.groupby(frame.INTERVAL_START.dt.floor("30min"))[column].agg(
            ["mean", "count"]
        )
        return grouped["mean"].where(grouped["count"].eq(6)).sort_index()

    return aggregate(prices, "RRP"), aggregate(dispatch, "intensity_t_per_mwh")


def history_features(signal: pd.Series, prefix: str, delay: timedelta) -> pd.DataFrame:
    """Calculate recent statistics and record when they become available for
    forecasting.
    """
    signal = signal.dropna().sort_index()
    frame = pd.DataFrame(
        {
            f"{prefix}_last": signal,
            f"{prefix}_mean_24h": signal.rolling("24h").mean(),
            f"{prefix}_std_24h": signal.rolling("24h", min_periods=2).std(),
            f"{prefix}_mean_7d": signal.rolling("7D").mean(),
        }
    )
    if prefix == "price":
        frame["price_negative_fraction_24h"] = (
            signal.lt(0).astype(float).rolling("24h").mean()
        )
    frame["interval_end"] = signal.index + AEMO_PREDISPATCH_INTERVAL
    frame.index = signal.index + AEMO_PREDISPATCH_INTERVAL + delay
    return frame


def emissions_profile_history(dispatch: pd.DataFrame) -> pd.DataFrame:
    """Group past generation and emissions into half-hour emissions intensities."""
    dispatch = dispatch.copy()
    dispatch["half_hour"] = pd.to_datetime(dispatch.INTERVAL_START).dt.floor("30min")
    history = (
        dispatch.groupby("half_hour")
        .agg(
            generation_mwh=("generation_mwh", "sum"),
            emissions_tco2e=("emissions_tco2e", "sum"),
        )
        .reset_index()
    )
    history["intensity"] = (history.emissions_tco2e / history.generation_mwh).replace(
        [np.inf, -np.inf], np.nan
    )
    history["slot"] = history.half_hour.dt.hour * 2 + history.half_hour.dt.minute // 30
    return history


def baseline_emissions(
    history: pd.DataFrame,
    run_market: datetime,
    targets: pd.DatetimeIndex,
) -> list[float]:
    """Estimate emissions for each time of day using seven days of delayed history."""
    cutoff = run_market - EMISSIONS_HISTORY_DELAY
    eligible = history.loc[
        (history.half_hour >= cutoff - timedelta(days=FORECAST_HISTORY_DAYS))
        & (history.half_hour < cutoff)
    ]
    profile = eligible.groupby("slot").intensity.median().dropna()
    slots = targets.hour * 2 + targets.minute // 30
    missing = set(slots) - set(profile.index)
    if missing:
        raise RuntimeError(
            "Insufficient historical emissions data for "
            f"{run_market.isoformat()}: "
            f"missing {len(missing)} half-hour profile slots in the preceding "
            f"{FORECAST_HISTORY_DAYS} days ending {cutoff.isoformat()}."
        )
    return [round(float(profile[slot]), 4) for slot in slots]


def build_forecast_features(
    run_market: datetime,
    prices: list[float],
    sources: list[PriceForecastSource],
    emissions: list[float],
    previous_prices: dict[pd.Timestamp, float],
    price_signal: pd.Series,
    emissions_signal: pd.Series,
    price_history: pd.DataFrame,
    emissions_history: pd.DataFrame,
) -> pd.DataFrame:
    """Combine baseline forecasts, time information and recent history into model
    inputs.
    """
    targets = pd.date_range(run_market, periods=len(prices), freq="30min")
    decision = pd.Timestamp(run_market + FORECAST_PUBLICATION_GRACE)
    slots = targets.hour * 2 + targets.minute // 30
    block = pd.DataFrame(
        {
            "h": np.arange(len(prices)) / 2,
            "baseline_price": prices,
            "price_is_historical": [int(source == "historical") for source in sources],
            "baseline_emissions": emissions,
            "target_slot": slots,
            "target_weekday": targets.dayofweek,
            "target_month": targets.month,
            "target_weekend": targets.dayofweek >= 5,
            "target_day_sin": np.sin(2 * np.pi * slots / 48),
            "target_day_cos": np.cos(2 * np.pi * slots / 48),
            "target_year_sin": np.sin(2 * np.pi * (targets.dayofyear - 1) / 365.25),
            "target_year_cos": np.cos(2 * np.pi * (targets.dayofyear - 1) / 365.25),
            "price_revision": [
                value - previous_prices.get(target, np.nan)
                for target, value in zip(targets, prices, strict=True)
            ],
        }
    )
    for prefix, history in (("price", price_history), ("emissions", emissions_history)):
        position = history.index.searchsorted(decision, side="right") - 1
        if position < 0:
            for name in history.columns.drop("interval_end"):
                block[name] = np.nan
            block[f"{prefix}_age_hours"] = np.nan
        else:
            row = history.iloc[position]
            for feature_name, value in row.drop("interval_end").items():
                block[str(feature_name)] = value
            block[f"{prefix}_age_hours"] = (
                decision - row["interval_end"]
            ).total_seconds() / 3600
    for prefix, signal, delay, lags in (
        ("price", price_signal, PRICE_PUBLICATION_DELAY, (1, 7)),
        ("emissions", emissions_signal, EMISSIONS_HISTORY_DELAY, (3, 7)),
    ):
        for lag in lags:
            historic_targets = targets - pd.Timedelta(days=lag)
            values = signal.reindex(historic_targets).to_numpy(copy=True)
            values[historic_targets + AEMO_PREDISPATCH_INTERVAL + delay > decision] = (
                np.nan
            )
            block[f"{prefix}_same_slot_{lag}d"] = values
    return block[FEATURE_COLUMNS].astype("float32")
