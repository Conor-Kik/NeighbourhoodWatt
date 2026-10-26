# AEMO

## Overview

This directory turns Australian electricity market data into prices and carbon
emissions estimates for our simulations. It downloads data, applies network
tariffs, and produces forecasts using historical patterns and saved machine
learning models.

## What each file does

| File | Brief explanation |
| --- | --- |
| [aemo_archive_client.py](aemo_archive_client.py) | Gets the raw data. Downloads AEMO ZIP archives, reads their CSV tables, and reuses cached downloads. It also retrieves interval data through NEMOSIS and provides time zone and region helpers. |
| [aemo_constants.py](aemo_constants.py) | Stores shared settings, such as market time zones, five-minute and half-hour intervals, cache locations, archive tables, and the inputs expected by the models. |
| [aemo_price_service.py](aemo_price_service.py) | Produces historical and forecast electricity prices. Builds forecasts from AEMO prices, fills missing forecast periods using historical patterns, applies saved model corrections by default, and adds network tariffs to calculate buying and selling rates. |
| [tariff_service.py](tariff_service.py) | Selects network import and export rates from tariff definitions using the location, date, and local time, including peak and off-peak periods. These are representative network rates for the simulation. |
| [aemo_emissions_service.py](aemo_emissions_service.py) | Calculates regional carbon intensity by combining generator output with generator emissions factors. It also forecasts intensity using historical patterns and saved model corrections by default. |
| [aemo_forecast_service.py](aemo_forecast_service.py) | Coordinates forecasting. Gathers historical data, prepares model inputs, selects saved models, and runs price or emissions predictions in batches while reusing source data. |
| [aemo_forecast_features.py](aemo_forecast_features.py) | Turns historical data into model inputs, such as recent averages, forecast revisions, time of day, and previous days' values. It respects publication delays so predictions only use information available at the simulated time. |
| [aemo_forecast_model_service.py](aemo_forecast_model_service.py) | Selects and loads the saved LightGBM model for the region, forecast type, and date. Checks that its metadata and expected inputs match, and caches loaded models. It does not train models. |
| [aemo_generation_forecast_service.py](aemo_generation_forecast_service.py) | Prepares regional solar availability, wind availability, and demand forecasts for the emissions model. Derives renewable share and demand remaining after wind and solar, using forecasts available at the decision time. |
| [cache_storage.py](cache_storage.py) | Measures disk space used by downloaded AEMO files and provides cache cleanup, with checks to prevent deletion outside the cache directory. |

## How the pieces fit together

1. The archive client retrieves and caches the source data.
2. The price and emissions services calculate historical values and baseline forecasts.
3. The forecast coordinator builds inputs and runs the saved models to adjust the baselines.
4. The price service applies network tariffs; both public services return structured results for the simulation pipeline.
