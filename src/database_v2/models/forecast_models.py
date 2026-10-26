"""Metadata and loaded models for annual-April AEMO forecast corrections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

ForecastKind = Literal["price", "emissions"]

# One shared forecast floor, calibrated on 2-31 March 2024 validation only.
# Wholesale AUD/MWh before tariffs; this is stricter than the NEM market floor.
PRICE_FORECAST_FLOOR_AUD_MWH = -75.0


class AemoForecastModelMetadata(BaseModel):
    """The inference contract retained alongside each native model file."""

    model_config = ConfigDict(frozen=True)
    region: str
    kind: ForecastKind
    fit_time_market: datetime
    prediction_end_market: datetime
    last_training_label_available_market: datetime
    retraining_schedule: str
    feature_columns: list[str]
    feature_version: int
    target_units: str
    prediction_type: str
    horizon_periods: int
    market_timezone: str
    publication_grace_minutes: float
    price_label_delay_minutes: float
    emissions_label_delay_hours: float


@dataclass(frozen=True)
class AemoForecastModel:
    """An eligible frozen model and the schema needed to apply its correction."""

    booster: lgb.Booster
    metadata: AemoForecastModelMetadata

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """Return corrected spot prices or nonnegative emissions intensity."""
        inputs = features[self.metadata.feature_columns].astype("float32")
        correction = self.booster.predict(inputs, num_threads=4, validate_features=True)
        values = features[f"baseline_{self.metadata.kind}"].to_numpy() + np.asarray(
            correction, dtype=float
        )
        if not np.isfinite(values).all():
            raise RuntimeError("LightGBM returned a non-finite forecast")
        floor = 0 if self.metadata.kind == "emissions" else PRICE_FORECAST_FLOOR_AUD_MWH
        return np.maximum(values, floor)
