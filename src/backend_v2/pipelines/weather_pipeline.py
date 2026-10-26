import logging
from datetime import timedelta
from pathlib import Path

from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from backend_v2.weather.fetch_weather import (
    WeatherDataRetrieval,
    latest_available_ecmwf_ensemble_init_time,
)
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.weather_dao import WeatherDao
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class WeatherPipeline(BaseDataPipeline):
    """Orchestrates coverage verification and population of weather data."""

    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        self.dao = WeatherDao(db_path)
        self.location_dao = LocationDao(db_path)
        self.weather_data_retrieval = WeatherDataRetrieval()

        location = self.location_dao.get_location(run_input.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {run_input.location_name}."
            )

        self.start_utc = run_input.start_time_utc
        self.end_utc = run_input.end_time_utc
        self.location_name = run_input.location_name
        self.latitude = location.latitude
        self.longitude = location.longitude

        self._ensure_openmeteo_weather()
        self._ensure_ecmwf_weather()

    def _ensure_openmeteo_weather(self) -> None:
        if self.dao.has_complete_openmeto_weather(
            self.start_utc, self.end_utc, self.location_name
        ):
            logger.debug("Open-Meteo weather already covers %s", self.location_name)
            return

        logger.info("Fetching Open-Meteo weather for %s", self.location_name)
        openmeteo_weather = self.weather_data_retrieval.fetch_openmeteo_weather(
            start_time=self.start_utc,
            end_time=self.end_utc,
            location_name=self.location_name,
            latitude=self.latitude,
            longitude=self.longitude,
        )

        if not openmeteo_weather:
            raise RuntimeError("Unable to fetch OpenMeteo weather data.")

        self.dao.save_openmeteo_weather(openmeteo_weather)
        logger.info("Stored %d Open-Meteo weather rows", len(openmeteo_weather))

    def _ensure_ecmwf_weather(self) -> None:
        run_count = int((self.end_utc - self.start_utc) / timedelta(minutes=30))
        cycles = {
            latest_available_ecmwf_ensemble_init_time(
                self.start_utc + timedelta(minutes=30 * run)
            ).to_pydatetime()
            for run in range(run_count)
        }
        missing_cycles = self.dao.get_missing_ecmwf_cycles(cycles, self.location_name)
        if not missing_cycles:
            logger.debug("ECMWF weather already covers %d cycles", len(cycles))
            return

        logger.info(
            "Fetching %d missing ECMWF weather cycles for %s",
            len(missing_cycles),
            self.location_name,
        )
        ecmwf_weather = self.weather_data_retrieval.fetch_ecmwf_cycles(
            cycles=missing_cycles,
            location_name=self.location_name,
            latitude=self.latitude,
            longitude=self.longitude,
        )

        if not ecmwf_weather:
            raise RuntimeError("Unable to fetch ECMWF weather data.")

        self.dao.save_emcwf_weather(ecmwf_weather)
        logger.info("Stored %d ECMWF weather rows", len(ecmwf_weather))
