import logging
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from backend_v2.aemo.aemo_emissions_service import IncompleteEmissionsDataError
from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from backend_v2.pipelines.demand_pipeline import DemandPipeline
from backend_v2.pipelines.emissions_pipeline import EmissionsPipeline
from backend_v2.pipelines.price_pipeline import PricePipeline
from backend_v2.pipelines.pv_pipeline import PvPipeline
from backend_v2.pipelines.weather_pipeline import WeatherPipeline
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


def ensure_data(
    run_input: SimulationRequest,
    db_path: Path,
    pipelines: Iterable[BaseDataPipeline] | None = None,
    callback: Callable | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Run the supplied pipelines, or all pipelines by default.
    Returns False if cancelled, True if all pipelines completed."""
    started = perf_counter()
    # If no particular pipelines requested, run them all in order
    if pipelines is None:
        # Include the next forecast issuance for terminal-energy valuation.
        run_input = run_input.model_copy(
            update={
                "end_time_utc": run_input.end_time_utc + timedelta(minutes=30),
            }
        )
        pipelines = (
            WeatherPipeline(),
            PvPipeline(),
            EmissionsPipeline(),
            PricePipeline(),
            DemandPipeline(),
        )

    logger.info(
        "Preparing data for %s (%s to %s)",
        run_input.location_name,
        run_input.start_time_utc.isoformat(),
        run_input.end_time_utc.isoformat(),
    )
    for pipeline in pipelines:
        name = type(pipeline).__name__
        if is_cancelled is not None and is_cancelled():
            logger.warning(f"Data retrieval cancelled before {name}")
            return False

        logger.info("Preparing %s", name)
        if callback:
            callback(f"Running {name}...")

        try:
            pipeline.ensure_data(run_input, db_path)
        except IncompleteEmissionsDataError as exc:
            logger.error("%s", exc)
            raise
        except Exception:
            logger.exception("%s failed for %s", name, run_input.location_name)
            raise
        logger.info("%s ready", name)
    logger.info(
        "Total startup data preparation time: %.2f seconds", perf_counter() - started
    )

    return True
