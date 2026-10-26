# Saved LightGBM seed models

Each `<region>/<year>/` directory contains gzip-compressed native LightGBM
weights (`price.txt.gz` and `emissions.txt.gz`) and matching JSON metadata.
The separate price and emissions models learn residual corrections to baseline
forecasts using MAE loss. Compression is lossless and requires only Python's
built-in gzip module.

Price models retain the original 28 features. Emissions models use
35 features, adding regional solar/wind availability, forecast demand,
residual demand, renewable share, forecast age and a missing-input flag.
`AemoGenerationForecastService` selects only eligible archived regional forecasts
for emissions inputs; price inference does not request generation forecasts.

All models start on 1 April of their directory year and remain eligible until
the following April. January-March uses the preceding year's April pair.
Training uses the preceding twelve months with a chronological validation
period. The 2026 evaluation covers April-August, while model eligibility
extends until April 2027. Metadata records feature order, units, training-label
availability and eligibility dates. Test data is not used for fitting.

The v2 AEMO services and database pipelines load these models by default.
`AemoForecastModelService` selects eligible models, validates metadata and
caches decompressed weights. `aemo_forecast_features.py` and
`aemo_forecast_service.py` construct the required features from information
available at the nominal solve; future realised labels are not inputs.
The four-minute publication grace is the historical replay assumption; archive
`LASTCHANGED` timestamps are database updates, not exact public download times.

Shared timing, paths and feature schemas live in `aemo_constants.py`.
The project's Conda environment installs LightGBM and its native runtime. See
[local setup](../../../../deployment/01-local-setup.md).

For callers with an already prepared feature frame:

```python
from backend_v2.aemo.aemo_forecast_model_service import AemoForecastModelService

service = AemoForecastModelService()
price_model = service.model_for_run(region, run_market, "price")
spot_aud_per_mwh = price_model.predict(features)
emissions_model = service.model_for_run(region, run_market, "emissions")
intensity_t_per_mwh = emissions_model.predict(features)
```

`run_market` is a naive datetime in AEMO market time. Use the feature columns
listed in the selected model's metadata. Prices are wholesale AUD/MWh: divide
by 1000 and apply v2 `TariffService` network tariffs before storing database
prices. Emissions intensity is tCO2e/MWh and is clipped at zero by the model
wrapper. Prefer the public price and emissions services when requesting full
forecast snapshots; they handle feature preparation and tariff application.

Simulation price and emissions pipelines always apply the saved models. Baseline
calculations remain necessary internally for feature preparation and residual
correction. Price source labels describe the underlying baseline input.
