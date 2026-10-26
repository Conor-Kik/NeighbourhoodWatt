from datetime import timedelta
from unittest.mock import Mock

from backend_v2 import pipeline_runner
from tests.factories import SimulationRequestFactory


def test_default_preparation_includes_terminal_forecast_without_extending_run(
    tmp_path, monkeypatch
) -> None:
    request = SimulationRequestFactory.build()
    pipelines = []
    for name in (
        "WeatherPipeline",
        "PvPipeline",
        "EmissionsPipeline",
        "PricePipeline",
        "DemandPipeline",
    ):
        pipeline = Mock()
        pipelines.append(pipeline)
        monkeypatch.setattr(pipeline_runner, name, Mock(return_value=pipeline))
    original_end = request.end_time_utc
    pipeline_runner.ensure_data(request, tmp_path / "data.db")
    for pipeline in pipelines:
        prepared = pipeline.ensure_data.call_args.args[0]
        assert prepared.end_time_utc == original_end + timedelta(minutes=30)
        assert prepared.start_time_utc == request.start_time_utc
    assert request.end_time_utc == original_end
