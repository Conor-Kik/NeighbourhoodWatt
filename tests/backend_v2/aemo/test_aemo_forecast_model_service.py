"""Check native model exports against the shared forecast schema."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend_v2.aemo.aemo_constants import MODEL_ROOT
from backend_v2.aemo.aemo_forecast_model_service import AemoForecastModelService


@pytest.mark.parametrize(
    ("region", "year", "kind"),
    [
        (region, year, kind)
        for region in ("NSW1", "QLD1", "SA1", "TAS1", "VIC1")
        for year in (2024, 2025, 2026)
        for kind in ("price", "emissions")
    ],
)
def test_saved_model_predicts_with_shared_schema(region, year, kind) -> None:
    service = AemoForecastModelService(MODEL_ROOT)
    model = service.model_for_run(region, datetime(year, 7, 1), kind)
    inputs = pd.DataFrame(0.0, index=range(2), columns=model.metadata.feature_columns)
    predictions = model.predict(inputs)
    assert predictions.shape == (2,)
    assert np.isfinite(predictions).all()
    if kind == "emissions":
        assert (predictions >= 0).all()
    # January must use the preceding April's export, including the cached model.
    assert service.model_for_run(region, datetime(year + 1, 1, 1), kind) is model
