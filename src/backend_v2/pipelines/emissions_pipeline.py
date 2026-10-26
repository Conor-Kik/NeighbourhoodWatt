import logging
from pathlib import Path

from backend_v2.aemo.aemo_emissions_service import AemoEmissionsService
from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from database_v2.data_access_objects.emissions_dao import EmissionsDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class EmissionsPipeline(BaseDataPipeline):
    """Orchestrates coverage verification and population of emissions data."""

    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        self.dao = EmissionsDao(db_path)
        self.location_dao = LocationDao(db_path)
        self.aemo_service = AemoEmissionsService()

        self.start_utc = run_input.start_time_utc
        self.end_utc = run_input.end_time_utc
        self.location_name = run_input.location_name

        try:
            self._ensure_realised_emissions()
            self._ensure_forecast_emissions()
        finally:
            self.dao.close()

    def _ensure_realised_emissions(self) -> None:
        if self.dao.has_complete_realised_emissions(
            self.start_utc,
            self.end_utc,
            self.location_name,
        ):
            logger.debug("Realised emissions already cover %s", self.location_name)
            return

        location = self.location_dao.get_location(self.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        logger.info("Loading realised emissions for %s", self.location_name)
        realised_emissions = self.aemo_service.calculate_realised_emissions(
            location=location,
            start_utc=self.start_utc,
            end_utc=self.end_utc,
        )

        if not realised_emissions:
            raise RuntimeError("AEMO Service returned no realised emissions.")

        self.dao.save_realised_emissions(realised_emissions)
        logger.info("Stored %d realised emissions rows", len(realised_emissions))

    def _ensure_forecast_emissions(self) -> None:
        if self.dao.has_complete_forecast_window(
            self.start_utc,
            self.end_utc,
            self.location_name,
        ):
            logger.debug("Forecast emissions already cover %s", self.location_name)
            return

        location = self.location_dao.get_location(self.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        logger.info("Building emissions forecasts for %s", self.location_name)
        forecast = self.aemo_service.calculate_forecast_emissions(
            location=location,
            start_utc=self.start_utc,
            end_utc=self.end_utc,
        )
        if not forecast:
            raise RuntimeError("AEMO Service returned no forecast emissions.")
        self.dao.save_forecast_emissions(forecast)
        logger.info("Stored %d emissions forecast snapshots", len(forecast))
