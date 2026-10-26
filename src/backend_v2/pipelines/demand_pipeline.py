"""Populate realised and daily forecast demand from synthetic load profiles."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend_v2.load_profiles.load_profile_dataclasses import LoadProfileGenerator
from backend_v2.pipelines.abc_pipeline import BaseDataPipeline
from database_v2.data_access_objects.demand_dao import DemandDao
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.models.data_models import ForecastDemandRow, RealisedDemandRow
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class DemandPipeline(BaseDataPipeline):
    """Orchestrates coverage verification and population of demand data."""

    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        self.dao = DemandDao(db_path)
        self.config_dao = HouseConfigDao(db_path)
        self.load_profile_generator = LoadProfileGenerator()

        self.start_utc = run_input.start_time_utc
        self.end_utc = run_input.end_time_utc
        self.houses = run_input.houses

        location = self.dao.conn.execute(
            "SELECT timezone FROM location WHERE location_name = ?",
            (run_input.location_name,),
        ).fetchone()
        if location is None:
            raise RuntimeError(f"Unknown demand location: {run_input.location_name}")
        zone = ZoneInfo(location[0])
        start_date = self.start_utc.astimezone(zone).date()
        last_realised_date = (
            (self.end_utc - timedelta(minutes=5)).astimezone(zone).date()
        )
        # The last rolling 24-hour solve targets through end_utc + 23 hours.
        last_forecast_date = (
            (self.end_utc + timedelta(hours=23.5)).astimezone(zone).date()
        )
        self._realised_start_times = [
            datetime.combine(
                start_date + timedelta(days=day), datetime.min.time(), tzinfo=UTC
            )
            for day in range((last_realised_date - start_date).days + 1)
        ]
        self._forecast_start_times = [
            datetime.combine(
                start_date + timedelta(days=day), datetime.min.time(), tzinfo=UTC
            )
            for day in range((last_forecast_date - start_date).days + 1)
        ]

        n = len(self.houses)
        for i, (house_config_id, num_houses) in enumerate(self.houses.items(), start=1):
            self._ensure_house_realised_demand(house_config_id, num_houses)
            self._ensure_house_forecast_demand(house_config_id)

            logger.info(
                "Demand ready for %d/%d house configs (%d%%)",
                i,
                n,
                round(i * 100 / n),
            )

        self.dao.close()
        self.config_dao.close()

    def _ensure_house_realised_demand(
        self, house_config_id: str, num_houses: int
    ) -> None:
        """"""
        if self.dao.has_complete_realised_demand(
            self._realised_start_times[0],
            self._realised_start_times[-1] + timedelta(days=1),
            house_config_id,
        ):
            logger.debug(f"Realised demand already covers {house_config_id}")
            return

        house_config = self.config_dao.get_house_config(house_config_id)

        if house_config is None:
            logger.error(f"Could not retrieve house config: {house_config_id}")
            raise RuntimeError(f"Could not retrieve house config: {house_config_id}")

        realised_demand_rows: list[RealisedDemandRow] = list()
        for start_time in self._realised_start_times:
            realised_demand_rows.extend(
                self.load_profile_generator.get_realised_load_profile(
                    house_config_id,
                    start_time,
                    num_houses,
                    has_aircon=house_config.has_aircon,
                    has_poolpump=house_config.has_poolpump,
                    has_ev=house_config.has_ev,
                )
            )

        self.dao.save_realised_demand(realised_demand_rows)

    def _ensure_house_forecast_demand(self, house_config_id: str) -> None:
        """"""
        if self.dao.has_complete_forecast_demand(
            self._forecast_start_times[0],
            self._forecast_start_times[-1] + timedelta(minutes=30),
            house_config_id,
        ):
            logger.debug(f"Forecast demand already covers {house_config_id}")
            return

        house_config = self.config_dao.get_house_config(house_config_id)

        if house_config is None:
            logger.error(f"Could not retrieve house config: {house_config_id}")
            raise RuntimeError(f"Could not retrieve house config: {house_config_id}")

        forecast_demand_rows: list[ForecastDemandRow] = list()
        for start_time in self._forecast_start_times:
            forecast_demand_rows.extend(
                self.load_profile_generator.get_forecasted_load_profile(
                    house_config_id,
                    start_time,
                    has_aircon=house_config.has_aircon,
                    has_poolpump=house_config.has_poolpump,
                    has_ev=house_config.has_ev,
                )
            )

        self.dao.save_forecast_demand(forecast_demand_rows)
