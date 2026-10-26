"""Check that emissions history reuse preserves the causal model inputs."""

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend_v2.aemo import aemo_forecast_service as fs
from backend_v2.aemo.aemo_constants import AEMO_MARKET_TIMEZONE
from backend_v2.aemo.aemo_emissions_service import AemoEmissionsService
from backend_v2.aemo.aemo_price_service import AemoPriceService


@pytest.mark.parametrize(
    ("now", "expected_loads"),
    [(datetime(2026, 8, 5), 1), (datetime(2026, 7, 20), 2)],
)
def test_emissions_cache_preserves_daily_inputs_and_region(
    monkeypatch, now, expected_loads
):
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = now.replace(tzinfo=AEMO_MARKET_TIMEZONE)
    monkeypatch.setattr(fs, "datetime", clock)
    times = pd.date_range("2026-07-01", "2026-08-01", freq="5min", inclusive="left")
    intensity = 0.4 + times.day / 100
    dispatch = pd.DataFrame(
        {
            "INTERVAL_START": times,
            "generation_mwh": 10.0,
            "emissions_tco2e": intensity * 10,
            "intensity_t_per_mwh": intensity,
        }
    )
    loader = MagicMock(return_value=dispatch)
    monkeypatch.setattr(AemoEmissionsService, "_load_realised_emissions", loader)
    monkeypatch.setattr(
        AemoPriceService,
        "_compile_dispatch_prices",
        lambda *args: pd.DataFrame(
            {"SETTLEMENTDATE": times + pd.Timedelta(minutes=5), "RRP": 100 + times.day}
        ),
    )
    monkeypatch.setattr(
        AemoPriceService,
        "_build_spot_run",
        lambda *args, **kwargs: ([100.0] * 48, ["aemo"] * 48),
    )
    predispatch = pd.DataFrame({"REGIONID": ["NSW1", "QLD1"], "INTERVENTION": [0, 0]})
    service = fs.AemoForecastService(MagicMock())
    for day in (15, 16):
        run = pd.Timestamp(datetime(2026, 7, day, 4))
        cached = service._features_for_batch("NSW1", [run], 48, predispatch, None)
        direct = service._features_for_batch("NSW1", [run], 48, predispatch, dispatch)
        pd.testing.assert_frame_equal(cached[run], direct[run])
        # Future rows in the prefetched month must not enter emissions features.
        assert cached[run].emissions_last.iloc[0] == pytest.approx(
            0.4 + (day - 2) / 100
        )

    assert loader.call_count == expected_loads
    first_window = loader.call_args_list[0].kwargs
    if expected_loads == 1:
        assert first_window["start_market"] == datetime(2026, 7, 1)
        assert first_window["end_market"] == datetime(2026, 8, 1)
    else:
        assert first_window["start_market"] == datetime(2026, 7, 5, 4)
        assert first_window["end_market"] == datetime(2026, 7, 13, 4, 4)

    other_region = dispatch.copy()
    other_region[["emissions_tco2e", "intensity_t_per_mwh"]] *= 2
    loader.return_value = other_region
    regional = service._features_for_batch("QLD1", [run], 48, predispatch, None)
    assert loader.call_count == expected_loads + 1
    assert loader.call_args.kwargs["region"] == "QLD1"
    assert regional[run].emissions_last.iloc[0] == pytest.approx(
        cached[run].emissions_last.iloc[0] * 2
    )
