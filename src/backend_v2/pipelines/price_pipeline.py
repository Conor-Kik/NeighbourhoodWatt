"""Populate realised and forecast prices for a simulation window."""

import logging
from datetime import timedelta
from pathlib import Path

from backend_v2.aemo.aemo_archive_client import utc_to_market_time
from backend_v2.aemo.aemo_price_service import AemoPriceService
from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.price_dao import PriceDao
from database_v2.models.data_models import ForecastPriceSnapshot
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class PricePipeline(BaseDataPipeline):
    """Orchestrates coverage verification and population of price data."""

    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        self.dao = PriceDao(db_path)
        self.location_dao = LocationDao(db_path)
        self.aemo_service = AemoPriceService()

        self.start_utc = run_input.start_time_utc
        self.end_utc = run_input.end_time_utc
        self.location_name = run_input.location_name

        try:
            self._ensure_realised_prices()
            self._ensure_forecast_prices()
        finally:
            self.dao.close()

    def _ensure_realised_prices(self) -> None:
        if self.dao.has_complete_realised_prices(
            self.start_utc,
            self.end_utc,
            self.location_name,
        ):
            logger.debug("Realised prices already cover %s", self.location_name)
            return

        location = self.location_dao.get_location(self.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        logger.info("Loading realised prices for %s", self.location_name)
        realised_prices = self.aemo_service.get_realised_prices(
            location=location,
            start_utc=self.start_utc,
            end_utc=self.end_utc,
        )

        if not realised_prices:
            raise RuntimeError("AEMO Service returned no realised prices.")

        self.dao.save_realised_prices(realised_prices)
        logger.info("Stored %d realised price rows", len(realised_prices))

    def _ensure_forecast_prices(self) -> None:
        if self.dao.has_complete_forecast_window(
            self.start_utc,
            self.end_utc,
            self.location_name,
        ):
            logger.debug("Forecast prices already cover %s", self.location_name)
            return

        location = self.location_dao.get_location(self.location_name)

        if location is None:
            raise RuntimeError(
                f"Could not retrieve location from db: {self.location_name}."
            )

        logger.info("Loading price forecasts for %s", self.location_name)
        forecast: list[ForecastPriceSnapshot] = []
        day_start = self.start_utc
        while day_start < self.end_utc:
            market_start = utc_to_market_time(day_start, "start_utc")
            daily = self.aemo_service.get_price_forecasts(
                location=location,
                start_time=market_start.time(),
                day=market_start.date(),
            )
            forecast.extend(
                snapshot
                for snapshot in daily
                if self.start_utc <= snapshot.run_time_utc < self.end_utc
            )
            day_start += timedelta(days=1)
        if not forecast:
            raise RuntimeError("AEMO Service returned no forecast prices.")
        expected_times = {
            self.start_utc + timedelta(minutes=30 * index)
            for index in range(
                int((self.end_utc - self.start_utc) / timedelta(minutes=30))
            )
        }
        if {snapshot.run_time_utc for snapshot in forecast} != expected_times:
            raise RuntimeError("AEMO Service returned incomplete price forecast runs.")
        self.dao.save_forecast_prices(forecast)
        logger.info("Stored %d price forecast snapshots", len(forecast))
