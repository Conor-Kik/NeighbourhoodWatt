"""Gathers data and runs saved models to produce price and emissions forecasts."""

from collections import OrderedDict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend_v2.aemo.aemo_archive_client import AemoArchiveClient, normalise_region
from backend_v2.aemo.aemo_constants import (
    AEMO_MARKET_TIMEZONE,
    AEMO_PREDISPATCH_INTERVAL,
    AEMO_PREDISPATCH_PERIODS_PER_DAY,
    EMISSIONS_HISTORY_DELAY,
    FEATURE_HISTORY_DAYS,
    FORECAST_HISTORY_DAYS,
    FORECAST_PUBLICATION_GRACE,
    PRICE_PUBLICATION_DELAY,
    RENEWABLE_FEATURE_COLUMNS,
)
from backend_v2.aemo.aemo_forecast_features import (
    baseline_emissions,
    build_forecast_features,
    emissions_profile_history,
    half_hour_history,
    history_features,
)
from backend_v2.aemo.aemo_forecast_model_service import AemoForecastModelService
from backend_v2.aemo.aemo_generation_forecast_service import (
    AemoGenerationForecastService,
)
from database_v2.models.forecast_models import ForecastKind
from database_v2.models.request_models import Location


class AemoForecastService:
    """Share causal inference across price/emissions without changing snapshots."""

    def __init__(
        self,
        client: AemoArchiveClient,
        model_service: AemoForecastModelService | None = None,
    ) -> None:
        """Set up data access, forecast models and a cache of reusable source tables."""
        self.client = client
        self.model_service = model_service or AemoForecastModelService()
        self.generation_forecasts = AemoGenerationForecastService(client)
        self._source_cache: OrderedDict[tuple[str, str, str, str], pd.DataFrame] = (
            OrderedDict()
        )

    def predict(
        self,
        *,
        location: Location,
        start_market: datetime,
        end_market: datetime,
        horizon_period_count: int,
        kind: ForecastKind,
        predispatch: pd.DataFrame | None = None,
        dispatch: pd.DataFrame | None = None,
    ) -> dict[datetime, np.ndarray]:
        """Run saved models in daily batches and return predictions grouped by forecast
        time.
        """
        runs = list(
            pd.date_range(start_market, end_market, freq="30min", inclusive="left")
        )
        region = normalise_region(location.nem_region)
        models = {
            run: self.model_service.model_for_run(
                region, run.to_pydatetime(), kind, horizon_period_count
            )
            for run in runs
        }
        predictions: dict[datetime, np.ndarray] = {}
        for offset in range(0, len(runs), AEMO_PREDISPATCH_PERIODS_PER_DAY):
            batch_runs = runs[offset : offset + AEMO_PREDISPATCH_PERIODS_PER_DAY]
            features = self._features_for_batch(
                region, batch_runs, horizon_period_count, predispatch, dispatch
            )
            if kind == "emissions":
                keys = pd.concat(
                    [
                        pd.DataFrame(
                            {
                                "run_time_market": run,
                                "decision_time_market": (
                                    run + FORECAST_PUBLICATION_GRACE
                                ),
                                "target_time_market": pd.date_range(
                                    run, periods=horizon_period_count, freq="30min"
                                ),
                            }
                        )
                        for run in batch_runs
                    ],
                    ignore_index=True,
                )
                renewable = self.generation_forecasts.features(keys, region)
                for index, run in enumerate(batch_runs):
                    start = index * horizon_period_count
                    features[run][RENEWABLE_FEATURE_COLUMNS] = renewable.iloc[
                        start : start + horizon_period_count
                    ].to_numpy()
            # A day crossing 1 April uses both cohorts, chosen by nominal solve.
            cohorts: dict[datetime, list[pd.Timestamp]] = {}
            for run in batch_runs:
                cohorts.setdefault(models[run].metadata.fit_time_market, []).append(run)
            for cohort_runs in cohorts.values():
                inputs = pd.concat(
                    [features[run] for run in cohort_runs], ignore_index=True
                )
                values = models[cohort_runs[0]].predict(inputs)
                for index, run in enumerate(cohort_runs):
                    start = index * horizon_period_count
                    predictions[run.to_pydatetime()] = values[
                        start : start + horizon_period_count
                    ]
        return predictions

    def _features_for_batch(
        self,
        region: str,
        runs: list[pd.Timestamp],
        count: int,
        predispatch: pd.DataFrame | None,
        dispatch: pd.DataFrame | None,
    ) -> dict[pd.Timestamp, pd.DataFrame]:
        # Imports stay here because public services also use this coordinator.
        """Build the model inputs for a batch of forecast runs in one region."""
        from backend_v2.aemo.aemo_emissions_service import AemoEmissionsService
        from backend_v2.aemo.aemo_price_service import AemoPriceService

        price_service = AemoPriceService(self.client, use_lightgbm=False)
        first, last = runs[0].to_pydatetime(), runs[-1].to_pydatetime()
        previous_run = first - AEMO_PREDISPATCH_INTERVAL
        history_start = first - timedelta(days=FEATURE_HISTORY_DAYS)
        price_end = last + FORECAST_PUBLICATION_GRACE
        if predispatch is None:
            key = (
                "predispatch",
                region,
                (previous_run - timedelta(days=FORECAST_HISTORY_DAYS)).strftime(
                    "%Y-%m"
                ),
                last.strftime("%Y-%m"),
            )
            if key not in self._source_cache:
                self._cache_source(
                    key,
                    price_service._load_predispatch_window(
                        first_snapshot=previous_run,
                        last_snapshot=last,
                    ),
                )
            predispatch = self._source_cache[key]
        predispatch = predispatch.loc[
            predispatch.REGIONID.eq(region) & predispatch.INTERVENTION.eq(0)
        ]
        key = (
            "dispatch_price",
            region,
            history_start.strftime("%Y-%m"),
            price_end.strftime("%Y-%m"),
        )
        if key not in self._source_cache:
            self._cache_source(
                key,
                price_service._compile_dispatch_prices(
                    region, history_start, price_end
                ),
            )
        prices = self._source_cache[key].copy()
        ends = pd.to_datetime(prices.SETTLEMENTDATE, format="mixed", errors="raise")
        prices = prices.loc[(ends > history_start) & (ends <= price_end)]
        emission_end = last - EMISSIONS_HISTORY_DELAY + FORECAST_PUBLICATION_GRACE
        if dispatch is None:
            load_start, load_end = history_start, emission_end
            month_end = (
                pd.Timestamp(emission_end).to_period("M") + 1
            ).start_time.to_pydatetime()
            # Prefetch completed months; the batch's causal window is sliced below.
            if month_end <= (
                datetime.now(AEMO_MARKET_TIMEZONE).replace(tzinfo=None)
                - EMISSIONS_HISTORY_DELAY
            ):
                load_start = history_start.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                load_end = month_end
            key = (
                "dispatch_emissions",
                region,
                load_start.isoformat(),
                load_end.isoformat(),
            )
            if key not in self._source_cache:
                self._cache_source(
                    key,
                    AemoEmissionsService(
                        self.client, use_lightgbm=False
                    )._load_realised_emissions(
                        region=region, start_market=load_start, end_market=load_end
                    ),
                )
            dispatch = self._source_cache[key]
        times = pd.to_datetime(dispatch.INTERVAL_START, format="mixed", errors="raise")
        dispatch = dispatch.loc[(times >= history_start) & (times < emission_end)]
        price_signal, emissions_signal = half_hour_history(prices, dispatch)
        price_history = history_features(price_signal, "price", PRICE_PUBLICATION_DELAY)
        emissions_history = history_features(
            emissions_signal, "emissions", EMISSIONS_HISTORY_DELAY
        )
        profile_history = emissions_profile_history(dispatch)
        previous_prices: dict[pd.Timestamp, float] = {}
        try:
            previous, _ = price_service._build_spot_run(
                data=predispatch,
                region=region,
                snapshot_time=previous_run,
                forecast_count=AEMO_PREDISPATCH_PERIODS_PER_DAY,
            )
            previous_targets = pd.date_range(
                previous_run, periods=AEMO_PREDISPATCH_PERIODS_PER_DAY, freq="30min"
            )
            previous_prices = dict(zip(previous_targets, previous, strict=True))
        except RuntimeError:
            # A missing previous forecast is an unavailable revision, not zero.
            pass
        features = {}
        for run in runs:
            baseline, sources = price_service._build_spot_run(
                data=predispatch,
                region=region,
                snapshot_time=run.to_pydatetime(),
                forecast_count=AEMO_PREDISPATCH_PERIODS_PER_DAY,
            )
            targets = pd.date_range(
                run, periods=AEMO_PREDISPATCH_PERIODS_PER_DAY, freq="30min"
            )
            emissions = baseline_emissions(
                profile_history, run.to_pydatetime(), targets
            )
            features[run] = (
                build_forecast_features(
                    run.to_pydatetime(),
                    baseline,
                    sources,
                    emissions,
                    previous_prices,
                    price_signal,
                    emissions_signal,
                    price_history,
                    emissions_history,
                )
                .iloc[:count]
                .copy()
            )
            previous_prices = dict(zip(targets, baseline, strict=True))
        return features

    def _cache_source(
        self,
        key: tuple[str, str, str, str],
        data: pd.DataFrame,
    ) -> None:
        """Cache a source table and discard the oldest tables when more than four are
        stored.
        """
        self._source_cache[key] = data
        while len(self._source_cache) > 4:
            self._source_cache.popitem(last=False)
