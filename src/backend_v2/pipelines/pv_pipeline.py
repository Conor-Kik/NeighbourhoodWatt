import logging
from datetime import timedelta
from pathlib import Path

from backend_v2.generation.pv_generation_service import PvGenerationService
from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.pv_dao import PvDao
from database_v2.data_access_objects.weather_dao import WeatherDao
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class PvPipeline(BaseDataPipeline):
    """Orchestrates coverage verification and generation of household PV data."""

    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        self.pv_dao = PvDao(db_path)
        self.weather_dao = WeatherDao(db_path)
        self.config_dao = HouseConfigDao(db_path)
        self.location_dao = LocationDao(db_path)
        self.pv_service = PvGenerationService()

        self.start_utc = run_input.start_time_utc
        self.end_utc = run_input.end_time_utc
        self.location_name = run_input.location_name
        self.house_config_ids = list(run_input.houses)
        self.house_configs = []
        for house_config_id in self.house_config_ids:
            cfg = self.config_dao.get_house_config(house_config_id)
            if cfg is None:
                raise RuntimeError(
                    f"Could not retrieve house config: {house_config_id}"
                )
            self.house_configs.append(cfg)

        self._ensure_realised_pv()
        self._ensure_forecast_pv()

    def _ensure_realised_pv(self) -> None:
        if self.pv_dao.has_complete_realised_pv(
            self.start_utc, self.end_utc, self.location_name, self.house_config_ids
        ):
            logger.debug("Realised PV already covers house configs for location.")
            return

        logger.info(
            "Generating realised PV for %d house configs", len(self.house_config_ids)
        )
        weather = self.weather_dao.get_openmeteo_weather(
            self.start_utc, self.end_utc, self.location_name
        )

        if not weather:
            raise RuntimeError("No OpenMeteo weather stored to calculate PV from.")

        location = self.location_dao.get_location(self.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        realised_pv = [
            row
            for house_config in self.house_configs
            for row in self.pv_service.calculate_realised_pv(
                location, house_config, weather, self.start_utc, self.end_utc
            )
        ]

        self.pv_dao.save_realised_pv(realised_pv)
        logger.info("Stored %d realised PV rows", len(realised_pv))

    def _ensure_forecast_pv(self) -> None:
        if self.pv_dao.has_complete_forecast_pv_window(
            self.start_utc, self.end_utc, self.location_name, self.house_config_ids
        ):
            logger.debug("Forecast PV already covers house configs for location.")
            return

        logger.info(
            f"Generating forecast PV for {len(self.house_config_ids)} house configs"
        )

        weather = self.weather_dao.get_ecmwf_weather(
            self.start_utc,
            self.end_utc + timedelta(hours=24),
            self.location_name,
        )
        if not weather:
            raise RuntimeError("No ECMWF weather stored to calculate PV from.")

        location = self.location_dao.get_location(self.location_name)
        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        forecast_pv = [
            snapshot
            for house_config in self.house_configs
            for snapshot in self.pv_service.calculate_forecast_pv(
                location, house_config, weather, self.start_utc, self.end_utc
            )
        ]

        self.pv_dao.save_forecast_pv(forecast_pv)
        logger.info(
            "Stored %d forecast PV snapshots (across %d configs)",
            len(forecast_pv),
            len(self.house_configs),
        )
