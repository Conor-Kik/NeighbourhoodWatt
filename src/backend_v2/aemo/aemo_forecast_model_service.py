"""Selects and loads saved price and emissions models for a region and forecast date."""

import gzip
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb

from backend_v2.aemo.aemo_archive_client import normalise_region
from backend_v2.aemo.aemo_constants import (
    AEMO_MARKET_TIMEZONE,
    AEMO_PREDISPATCH_PERIODS_PER_DAY,
    EMISSIONS_HISTORY_DELAY,
    FEATURE_COLUMNS_BY_KIND,
    FORECAST_PUBLICATION_GRACE,
    MODEL_ROOT,
    PRICE_PUBLICATION_DELAY,
)
from database_v2.models.forecast_models import (
    AemoForecastModel,
    AemoForecastModelMetadata,
    ForecastKind,
)


@lru_cache(maxsize=32)
def _load_model(
    model_dir: Path,
    region: str,
    year: int,
    kind: ForecastKind,
    model_modified: int,
    metadata_modified: int,
) -> AemoForecastModel:
    """Load and validate saved model files, reusing them until the files change."""
    metadata = AemoForecastModelMetadata.model_validate_json(
        (model_dir / f"{kind}.json").read_text(encoding="utf-8")
    )
    expected_fit, expected_end = datetime(year, 4, 1), datetime(year + 1, 4, 1)
    units = "AUD/MWh" if kind == "price" else "tCO2e/MWh"
    if (
        metadata.region != region
        or metadata.kind != kind
        or metadata.fit_time_market != expected_fit
        or metadata.prediction_end_market != expected_end
        or metadata.last_training_label_available_market > expected_fit
        or metadata.retraining_schedule != "annual_april"
        or metadata.feature_columns != FEATURE_COLUMNS_BY_KIND[kind]
        or metadata.prediction_type != "baseline_plus_residual"
        or metadata.target_units != units
        or metadata.horizon_periods != AEMO_PREDISPATCH_PERIODS_PER_DAY
        or metadata.market_timezone != AEMO_MARKET_TIMEZONE.key
        or metadata.publication_grace_minutes
        != FORECAST_PUBLICATION_GRACE.total_seconds() / 60
        or metadata.price_label_delay_minutes
        != PRICE_PUBLICATION_DELAY.total_seconds() / 60
        or metadata.emissions_label_delay_hours
        != EMISSIONS_HISTORY_DELAY.total_seconds() / 3600
    ):
        raise ValueError(f"Incompatible LightGBM metadata: {model_dir}/{kind}.json")
    model_path = model_dir / f"{kind}.txt.gz"
    with gzip.open(model_path, "rt", encoding="utf-8") as model_file:
        booster = lgb.Booster(model_str=model_file.read())
    if booster.feature_name() != metadata.feature_columns:
        raise ValueError(f"LightGBM feature schema differs: {model_path}")
    return AemoForecastModel(booster=booster, metadata=metadata)


class AemoForecastModelService:
    """Select only the region/year model available at a nominal market solve."""

    def __init__(self, model_root: Path = MODEL_ROOT) -> None:
        """Set the folder containing the saved forecast models."""
        self.model_root = model_root.resolve()

    def model_for_run(
        self,
        region: str,
        run_market: datetime,
        kind: ForecastKind,
        horizon_period_count: int = AEMO_PREDISPATCH_PERIODS_PER_DAY,
    ) -> AemoForecastModel:
        """Choose the saved regional model available at the requested forecast date."""
        if run_market.tzinfo is not None:
            raise ValueError("Model selection requires naive AEMO market time")
        if not 0 < horizon_period_count <= AEMO_PREDISPATCH_PERIODS_PER_DAY:
            raise ValueError("LightGBM forecasts require between 1 and 48 half-hours")
        region = normalise_region(region)
        year = run_market.year if run_market.month >= 4 else run_market.year - 1
        model_dir = self.model_root / region / str(year)
        model_path, metadata_path = (
            model_dir / f"{kind}.txt.gz",
            model_dir / f"{kind}.json",
        )
        if not model_path.exists() and not metadata_path.exists():
            raise FileNotFoundError(
                f"Missing LightGBM forecast model: {model_dir}/{kind}"
            )
        if not model_path.is_file() or not metadata_path.is_file():
            raise RuntimeError(f"Incomplete LightGBM model export: {model_dir}/{kind}")
        model = _load_model(
            model_dir,
            region,
            year,
            kind,
            model_path.stat().st_mtime_ns,
            metadata_path.stat().st_mtime_ns,
        )
        if (
            not model.metadata.fit_time_market
            <= run_market
            < model.metadata.prediction_end_market
        ):
            raise ValueError("LightGBM model is not eligible for this forecast run")
        return model
