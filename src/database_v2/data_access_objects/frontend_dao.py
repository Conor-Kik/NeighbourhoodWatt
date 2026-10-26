import inspect
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    PERIODS_PER_DAY,
)
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.demand_dao import register_demand_clock
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.run_dao import RunDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.db_constants import DB_PATH
from database_v2.models.request_models import (
    BatteryConfig,
    HouseConfig,
    Location,
    SimulationRequest,
)
from database_v2.utils.enforce_get_set import enforce_get_set
from database_v2.utils.forecast_issue import forecast_issue_at
from database_v2.utils.register_dtype import register_dtype

logger = logging.getLogger(__name__)

# Time between the points of the get_current_* getters that downsample the 5 minute
# outputs, so a run fits a buffer. Must divide an hour.
TIMELINE_STEP_MINUTES = 30
# The scenario whose grid import, export and curtailment the run's energy flows show
FLOWS_SCENARIO = "optimised_battery"

# Recorded financial cost per executed interval; carbon weighting and terminal
# credits belong to the result comparisons, not operating expenditure.
STEP_COST_SQL = """o.grid_import_kw * :hours * p.buy_tariff_aud
                  - o.grid_export_kw * :hours * p.sell_tariff_aud
                  + o.cycle_throughput_kwh * :degradation"""


@enforce_get_set
class FrontendDao:
    """
    Exclusive DAO for frontend database access. Translates between frontend
    dicts and backend Pydantic models. All public methods are getters / setters.
    """

    def __init__(self, db_path: str | Path = DB_PATH):
        self.location_dao = LocationDao(db_path)
        self.house_dao = HouseConfigDao(db_path)
        self.battery_dao = BatteryDao(db_path)
        # request_id of the run the app shows; the get_current_* getters return
        # that run's outputs. Kept in memory only, so no run is picked on start.
        self.current_request_id: str | None = None
        self.run_dao = RunDao(db_path)
        self.db_path = db_path

    @property
    def method_registry(self) -> dict[str, Any]:
        """Create dictionary mapping of string to methods, no editing is required."""
        registry = {
            name: value
            for name, value in FrontendDao.__dict__.items()
            if not name.startswith("_")
            and not isinstance(value, property)
            and (
                inspect.isfunction(value)
                or isinstance(value, (classmethod | staticmethod))
            )
        }

        return registry

    # ==========================================
    # READS (Must start with "get_")
    # ==========================================

    @register_dtype(
        [
            ("location_name", "U50"),
            ("latitude", "f8"),
            ("longitude", "f8"),
            ("timezone", "U50"),
            ("nem_region", "U10"),
        ]
    )
    def get_all_locations(self) -> list[dict]:
        """
        Fetches all available locations for UI dropdowns.
        Returns a list of dicts:
        [{'location_name': 'Brisbane', 'nem_region': 'QLD1', ...}, ...]
        """

        locs = self.location_dao.get_all_locations()

        return [loc.model_dump() for loc in locs]

    @register_dtype(
        [
            ("house_config_id", "U64"),
            ("pv_peak_capacity", "f8"),
            ("panel_tilt", "f8"),
            ("panel_azimuth", "f8"),
            ("pv_inverter_ac_kw", "f8"),
            ("pv_inverter_efficiency", "f8"),
            ("has_aircon", "?"),
            ("has_poolpump", "?"),
            ("has_ev", "?"),
        ]
    )
    def get_all_house_configs(self) -> list[dict]:
        """
        Fetches all available house configs for UI dropdowns.
        Returns a list of dicts:
        [{'house_config_id': 'placeholder-config', 'pv_peak_capacity': 5.0, ...}, ...]
        """

        configs = self.house_dao.get_all_house_configs()

        return [config.model_dump() for config in configs]

    @register_dtype(
        [
            ("battery_config_id", "U64"),
            ("capacity_kwh", "f8"),
            ("max_power_kw", "f8"),
            ("efficiency", "f8"),
            ("min_soc_frac", "f8"),
            ("max_soc_frac", "f8"),
            ("replacement_cost_aud", "f8"),
            ("lifetime_cycles", "i8"),
            ("max_cycles_24h", "f8"),
        ]
    )
    def get_all_battery_configs(self) -> list[dict]:
        """
        Fetches all available battery configs for UI dropdowns.
        Returns a list of dicts:
        [{'battery_config_id': 'placeholder-config', 'capacity_kwh': 200.0, ...}, ...]
        """

        configs = self.battery_dao.get_all_battery_configs()

        return [config.model_dump() for config in configs]

    # row is always 0, so picking another run overwrites the buffered one
    @register_dtype([("row", "i8"), ("request_id", "U16")])
    def get_current_request(self) -> list[dict]:
        """
        Fetches the request_id of the run the app shows. Empty if no run is
        picked.
        Returns a list of at most one dict:
        [{'row': 0, 'request_id': 'a1b2c3d4e5f6a7b8'}]
        """

        if self.current_request_id is None:
            return []

        return [{"row": 0, "request_id": self.current_request_id}]

    @register_dtype(
        [
            ("request_id", "U16"),
            ("objective_mode", "U50"),
            ("location_name", "U50"),
            ("start_time_utc", "U32"),
            ("end_time_utc", "U32"),
            ("battery_config_id", "U64"),
            ("house_count", "i8"),
            ("fixed_buy_aud_per_kwh", "f8"),
            ("fixed_sell_aud_per_kwh", "f8"),
        ]
    )
    def get_all_simulation_requests(self) -> list[dict]:
        """
        Fetches every saved simulation request (run), for picking which run the
        app shows. Times are ISO 8601 text; house_count totals every house.
        Returns a list of dicts:
        [{'request_id': '406fec1bb57264b0', 'location_name': 'Brisbane', ...}, ...]
        """

        requests = self.run_dao.get_all_simulation_requests()

        return [
            {
                "request_id": request.request_id,
                "objective_mode": request.objective_mode.value,
                "location_name": request.location_name,
                "start_time_utc": request.start_time_utc.isoformat(),
                "end_time_utc": request.end_time_utc.isoformat(),
                "battery_config_id": request.battery_config_id,
                "house_count": sum(request.houses.values()),
                "fixed_buy_aud_per_kwh": request.fixed_buy_aud_per_kwh,
                "fixed_sell_aud_per_kwh": request.fixed_sell_aud_per_kwh,
            }
            for request in requests
        ]

    # Starts with timestamp_utc: buffers key on the first field
    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("soc_kwh", "f8"),
            ("charge_kw", "f8"),
            ("discharge_kw", "f8"),
            ("grid_import_kw", "f8"),
            ("grid_export_kw", "f8"),
            ("curtailed_pv_kw", "f8"),
        ]
    )
    def get_current_optimiser_outputs(self) -> list[dict]:
        """
        Fetches the current run's 30 minute optimiser outputs, oldest first.
        Empty if no run is picked. Rows carry request_id, so rows left over from
        a previously picked run can be told apart.
        Returns a list of dicts:
        [{'timestamp_utc': '2025-12-31T18:00:00+00:00', 'soc_kwh': 40.0, ...}, ...]
        """

        request_id = self.current_request_id
        if request_id is None:
            return []

        return self.run_dao.get_optimiser_outputs(request_id)

    # Starts with scenario_name: buffers key on the first field
    @register_dtype(
        [
            ("scenario_name", "U50"),
            ("request_id", "U16"),
            ("battery_degradation_aud", "f8"),
            ("grid_import_aud", "f8"),
            ("grid_export_aud", "f8"),
            ("emissions_tonnes", "f8"),
            ("emissions_cost_aud", "f8"),
            ("terminal_soc_kwh", "f8"),
            ("estimated_terminal_cost_value_aud", "f8"),
            ("estimated_terminal_emissions_value_tonnes", "f8"),
            ("terminal_value_horizon_hours", "f8"),
        ]
    )
    def get_current_simulation_summaries(self) -> list[dict]:
        """
        Fetches the current run's summary totals, one per scenario. Empty if no
        run is picked. Terminal values missing for a scenario are NaN.
        Returns a list of dicts:
        [{'scenario_name': 'optimised_battery', 'grid_import_aud': 120.5, ...}, ...]
        """

        request_id = self.current_request_id
        if request_id is None:
            return []

        summaries = self.run_dao.get_simulation_summaries(request_id)

        # None can't be held by an f8 column
        return [
            {key: math.nan if value is None else value for key, value in row.items()}
            for row in summaries
        ]

    def _get_current_live_controller_outputs(self, scenario_name: str) -> list[dict]:
        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT timestamp_utc, request_id, scenario_name, soc_kwh,
                          charge_kw, discharge_kw, grid_import_kw, grid_export_kw,
                          curtailed_pv_kw, cycle_throughput_kwh, reasons
                   FROM live_controller_output_5m
                   WHERE request_id = ? AND scenario_name = ?
                   ORDER BY timestamp_utc""",
                (request_id, scenario_name),
            ).fetchall()
        return [dict(row) for row in rows]

    # Starts with point_id: buffers key on the first field, and one scenario's
    # timestamps repeat in the others
    @register_dtype(
        [
            ("point_id", "U80"),
            ("request_id", "U16"),
            ("scenario_name", "U50"),
            ("timestamp_utc", "U32"),
            ("soc_percent", "f8"),
            ("cumulative_cost_aud", "f8"),
        ]
    )
    def get_current_scenario_timeline(self) -> list[dict]:
        """
        Fetches the current run's battery scenarios, and the shared no battery
        baseline, over time: one point at the end of every TIMELINE_STEP_MINUTES
        (the 5 minute outputs would overflow a buffer). Empty if no run is picked.
        cumulative_cost_aud is the running grid import cost minus export revenue
        plus battery degradation, from the run's start. soc_percent is the stored
        energy as a percentage of the battery's nominal capacity; NaN with no
        battery. point_id is "<timestamp>|<scenario>", so points sort by time.
        Returns a list of dicts:
        [{'point_id': '2025-12-31T18:25:00+00:00|optimised_battery', ...}, ...]
        """

        request_id = self.current_request_id
        if request_id is None:
            return []
        request = self._request_for_id(request_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            register_demand_clock(conn, request.location_name)
            battery_row = conn.execute(
                "SELECT * FROM battery_config WHERE battery_config_id = ?",
                (request.battery_config_id,),
            ).fetchone()
            battery = BatteryConfig(**dict(battery_row))
            rows = conn.execute(
                f"""WITH community AS (
                       SELECT v.timestamp_utc,
                              SUM((d.demand_kw - v.pv_kw) * sh.quantity) AS net_kw
                       FROM simulation_house sh
                       JOIN realised_demand_5m d
                         ON d.house_config_id = sh.house_config_id
                       JOIN realised_pv_5m v
                         ON d.timestamp_utc = demand_time(v.timestamp_utc)
                        AND v.house_config_id = d.house_config_id
                        AND v.location_name = :location
                       WHERE sh.request_id = :request_id
                         AND v.timestamp_utc >= :start AND v.timestamp_utc < :end
                       GROUP BY v.timestamp_utc
                   ),
                   step AS (
                       SELECT o.timestamp_utc, o.scenario_name, o.soc_kwh,
                              {STEP_COST_SQL} AS cost_aud
                       FROM live_controller_output_5m o
                       JOIN realised_pricing_5m p
                         ON p.timestamp_utc = o.timestamp_utc
                        AND p.location_name = :location
                       WHERE o.request_id = :request_id
                       UNION ALL
                       SELECT c.timestamp_utc, 'no_battery_shared', NULL,
                              MAX(c.net_kw, 0) * :hours
                                * COALESCE(:fixed_buy, p.buy_tariff_aud)
                              - MAX(-c.net_kw, 0) * :hours
                                * COALESCE(:fixed_sell, p.sell_tariff_aud)
                       FROM community c
                       JOIN realised_pricing_5m p
                         ON p.timestamp_utc = c.timestamp_utc
                        AND p.location_name = :location
                   ),
                   running AS (
                       SELECT timestamp_utc, scenario_name, soc_kwh,
                              SUM(cost_aud) OVER (
                                  PARTITION BY scenario_name
                                  ORDER BY timestamp_utc
                              ) AS cumulative_cost_aud
                       FROM step
                   )
                   SELECT * FROM running
                   WHERE CAST(substr(timestamp_utc, 15, 2) AS INTEGER) % :timeline_step
                         = :timeline_step - :step_minutes
                   ORDER BY timestamp_utc, scenario_name""",
                {
                    "hours": LIVE_CONTROL_STEP_MINUTES / 60,
                    "degradation": battery.degradation_aud_per_kwh,
                    "fixed_buy": request.fixed_buy_aud_per_kwh,
                    "fixed_sell": request.fixed_sell_aud_per_kwh,
                    "location": request.location_name,
                    "request_id": request_id,
                    "start": request.start_time_utc.isoformat(),
                    "end": request.end_time_utc.isoformat(),
                    "step_minutes": LIVE_CONTROL_STEP_MINUTES,
                    "timeline_step": TIMELINE_STEP_MINUTES,
                },
            ).fetchall()
        return [
            {
                "point_id": f"{row['timestamp_utc']}|{row['scenario_name']}",
                "request_id": request_id,
                "scenario_name": row["scenario_name"],
                "timestamp_utc": row["timestamp_utc"],
                # None can't be held by an f8 column
                "soc_percent": (
                    math.nan
                    if row["soc_kwh"] is None
                    else row["soc_kwh"] / battery.capacity_kwh * 100
                ),
                "cumulative_cost_aud": row["cumulative_cost_aud"],
            }
            for row in rows
        ]

    @register_dtype(
        [
            ("request_id", "U16"),
            ("timestamp_utc", "U32"),
            ("cumulative_cost_aud", "f8"),
            ("recent_cost_aud", "f8"),
            ("recent_minutes", "i4"),
        ]
    )
    def get_current_replay_cost(
        self, replay_time_utc: datetime | None = None
    ) -> list[dict]:
        """Exact cost of completed intervals before the replay cursor.

        Return one snapshot, avoiding the chart buffers' history sampling. Missing
        intervals or prices leave the card unavailable rather than understate cost.
        """
        if self.current_request_id is None or replay_time_utc is None:
            return []
        request = self._request_for_id(self.current_request_id)
        end = min(max(replay_time_utc, request.start_time_utc), request.end_time_utc)
        recent_start = max(request.start_time_utc, end - timedelta(minutes=30))
        battery = self.battery_dao.get_battery_config(request.battery_config_id)
        if battery is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"""WITH steps AS (
                        SELECT o.timestamp_utc, {STEP_COST_SQL} AS cost_aud
                        FROM live_controller_output_5m o
                        LEFT JOIN realised_pricing_5m p
                          ON p.timestamp_utc = o.timestamp_utc
                         AND p.location_name = :location
                        WHERE o.request_id = :request_id
                          AND o.scenario_name = :scenario
                          AND o.timestamp_utc >= :start AND o.timestamp_utc < :end
                    )
                    SELECT COUNT(*) AS steps, COUNT(cost_aud) AS priced_steps,
                           COALESCE(SUM(cost_aud), 0) AS total,
                           COALESCE(SUM(CASE WHEN timestamp_utc >= :recent_start
                                            THEN cost_aud ELSE 0 END), 0) AS recent
                    FROM steps""",
                {
                    "hours": LIVE_CONTROL_STEP_MINUTES / 60,
                    "degradation": battery.degradation_aud_per_kwh,
                    "location": request.location_name,
                    "request_id": self.current_request_id,
                    "scenario": FLOWS_SCENARIO,
                    "start": request.start_time_utc.isoformat(),
                    "end": end.isoformat(),
                    "recent_start": recent_start.isoformat(),
                },
            ).fetchone()
        expected_steps = (end - request.start_time_utc).total_seconds() / (
            LIVE_CONTROL_STEP_MINUTES * 60
        )
        if row["steps"] != expected_steps or row["priced_steps"] != row["steps"]:
            return []
        return [
            {
                "request_id": self.current_request_id,
                "timestamp_utc": end.isoformat(),
                "cumulative_cost_aud": row["total"],
                "recent_cost_aud": row["recent"],
                "recent_minutes": int((end - recent_start).total_seconds() / 60),
            }
        ]

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("demand_kw", "f8"),
            ("pv_kw", "f8"),
            ("grid_import_kw", "f8"),
            ("grid_export_kw", "f8"),
            ("curtailed_pv_kw", "f8"),
            ("charge_kw", "f8"),
            ("discharge_kw", "f8"),
        ]
    )
    def get_current_energy_flows(self) -> list[dict]:
        """
        Fetches the current run's community power over time, one point per
        TIMELINE_STEP_MINUTES (the 5 minute outputs would overflow a buffer), each
        the average over its step and stamped with the step's start. Demand and PV
        are summed over every house of the run; grid import, export, curtailed PV
        and battery charge and discharge are those of the FLOWS_SCENARIO battery.
        Empty if no run is picked.
        Returns a list of dicts:
        [{'timestamp_utc': '2025-12-31T18:00:00+00:00', 'demand_kw': 31.5, ...}, ...]
        """

        request_id = self.current_request_id
        if request_id is None:
            return []
        request = self._request_for_id(request_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            register_demand_clock(conn, request.location_name)
            rows = conn.execute(
                """WITH community AS (
                       SELECT v.timestamp_utc,
                              SUM(d.demand_kw * sh.quantity) AS demand_kw,
                              SUM(v.pv_kw * sh.quantity) AS pv_kw
                       FROM simulation_house sh
                       JOIN realised_demand_5m d
                         ON d.house_config_id = sh.house_config_id
                       JOIN realised_pv_5m v
                         ON d.timestamp_utc = demand_time(v.timestamp_utc)
                        AND v.house_config_id = d.house_config_id
                        AND v.location_name = :location
                       WHERE sh.request_id = :request_id
                         AND v.timestamp_utc >= :start AND v.timestamp_utc < :end
                       GROUP BY v.timestamp_utc
                   )
                   SELECT substr(c.timestamp_utc, 1, 14)
                          || printf('%02d',
                                    CAST(substr(c.timestamp_utc, 15, 2) AS INTEGER)
                                    / :timeline_step * :timeline_step)
                          || substr(c.timestamp_utc, 17) AS timestamp_utc,
                          AVG(c.demand_kw) AS demand_kw,
                          AVG(c.pv_kw) AS pv_kw,
                          AVG(o.grid_import_kw) AS grid_import_kw,
                          AVG(o.grid_export_kw) AS grid_export_kw,
                          AVG(o.curtailed_pv_kw) AS curtailed_pv_kw,
                          AVG(o.charge_kw) AS charge_kw,
                          AVG(o.discharge_kw) AS discharge_kw
                   FROM community c
                   JOIN live_controller_output_5m o
                     ON o.timestamp_utc = c.timestamp_utc
                    AND o.request_id = :request_id
                    AND o.scenario_name = :scenario
                   GROUP BY 1
                   ORDER BY 1""",
                {
                    "location": request.location_name,
                    "request_id": request_id,
                    "start": request.start_time_utc.isoformat(),
                    "end": request.end_time_utc.isoformat(),
                    "timeline_step": TIMELINE_STEP_MINUTES,
                    "scenario": FLOWS_SCENARIO,
                },
            ).fetchall()
        return [{**dict(row), "request_id": request_id} for row in rows]

    # Starts with house_config_id: buffers key on the first field
    @register_dtype(
        [
            ("house_config_id", "U64"),
            ("request_id", "U16"),
            ("quantity", "i8"),
            ("pv_peak_capacity", "f8"),
            ("pv_inverter_ac_kw", "f8"),
            ("has_aircon", "?"),
            ("has_poolpump", "?"),
            ("has_ev", "?"),
        ]
    )
    def get_current_run_houses(self) -> list[dict]:
        """
        Fetches the house configs the current run used, with how many houses of
        each. Empty if no run is picked. Rows carry request_id, so rows left over
        from a previously picked run can be told apart.
        Returns a list of dicts:
        [{'house_config_id': 'house-1--6.6KW-5KW-INV', 'quantity': 20, ...}, ...]
        """

        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT h.house_config_id, sh.request_id, sh.quantity,
                          h.pv_peak_capacity, h.pv_inverter_ac_kw,
                          h.has_aircon, h.has_poolpump, h.has_ev
                   FROM simulation_house sh
                   JOIN house_config h ON h.house_config_id = sh.house_config_id
                   WHERE sh.request_id = ?
                   ORDER BY h.house_config_id""",
                (request_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("scenario_name", "U50"),
            ("soc_kwh", "f8"),
            ("charge_kw", "f8"),
            ("discharge_kw", "f8"),
            ("grid_import_kw", "f8"),
            ("grid_export_kw", "f8"),
            ("curtailed_pv_kw", "f8"),
            ("cycle_throughput_kwh", "f8"),
            ("reasons", "U1024"),
        ]
    )
    def get_current_optimised_battery_outputs(self) -> list[dict]:
        return self._get_current_live_controller_outputs("optimised_battery")

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("scenario_name", "U50"),
            ("soc_kwh", "f8"),
            ("charge_kw", "f8"),
            ("discharge_kw", "f8"),
            ("grid_import_kw", "f8"),
            ("grid_export_kw", "f8"),
            ("curtailed_pv_kw", "f8"),
            ("cycle_throughput_kwh", "f8"),
            ("reasons", "U1024"),
        ]
    )
    def get_current_reactive_battery_outputs(self) -> list[dict]:
        return self._get_current_live_controller_outputs("reactive_battery")

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("scenario_name", "U50"),
            ("soc_kwh", "f8"),
            ("charge_kw", "f8"),
            ("discharge_kw", "f8"),
            ("grid_import_kw", "f8"),
            ("grid_export_kw", "f8"),
            ("curtailed_pv_kw", "f8"),
            ("cycle_throughput_kwh", "f8"),
            ("reasons", "U1024"),
        ]
    )
    def get_current_perfect_foresight_outputs(self) -> list[dict]:
        return self._get_current_live_controller_outputs("perfect_foresight")

    @register_dtype(
        [
            ("run_time_utc", "U32"),
            ("target_time_utc", "U32"),
            ("request_id", "U16"),
            ("demand_kw", "f8"),
            ("pv_kw", "f8"),
            ("buy_tariff_aud_per_kwh", "f8"),
            ("sell_tariff_aud_per_kwh", "f8"),
            ("carbon_intensity_t_per_mwh", "f8"),
        ],
        pk=("run_time_utc", "target_time_utc"),
    )
    def get_current_forecast_data(
        self, replay_time_utc: datetime | None = None
    ) -> list[dict]:
        """The active optimiser issue's full horizon, using the engine's loader.

        One issue stays below the frontend buffer's sampling threshold. Missing
        forecast inputs produce an empty chart while other dashboard data loads.
        """
        if self.current_request_id is None:
            return []
        request = self._request_for_id(self.current_request_id)
        issue = forecast_issue_at(
            request.start_time_utc,
            request.end_time_utc,
            replay_time_utc if replay_time_utc is not None else request.start_time_utc,
        )
        if issue is None:
            return []
        dao = SimulationDao(self.db_path, request)
        try:
            try:
                forecast = dao.get_forecast_window(issue)
            except (RuntimeError, ValueError) as exc:
                logger.debug(
                    "Forecast unavailable for %s at %s: %s",
                    request.request_id,
                    issue,
                    exc,
                )
                return []
        finally:
            dao.close()
        if any(
            len(values) != PERIODS_PER_DAY
            for values in (
                forecast.target_times_utc,
                forecast.demand_kw,
                forecast.pv_kw,
                forecast.buy_tariff_aud_per_kwh,
                forecast.sell_tariff_aud_per_kwh,
                forecast.carbon_intensity_t_per_mwh,
            )
        ):
            return []
        return [
            {
                "run_time_utc": issue.isoformat(),
                "target_time_utc": target.isoformat(),
                "request_id": request.request_id,
                "demand_kw": demand,
                "pv_kw": pv,
                "buy_tariff_aud_per_kwh": buy,
                "sell_tariff_aud_per_kwh": sell,
                "carbon_intensity_t_per_mwh": emissions,
            }
            for target, demand, pv, buy, sell, emissions in zip(
                forecast.target_times_utc,
                forecast.demand_kw,
                forecast.pv_kw,
                forecast.buy_tariff_aud_per_kwh,
                forecast.sell_tariff_aud_per_kwh,
                forecast.carbon_intensity_t_per_mwh,
                strict=True,
            )
        ]

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("demand_kw", "f8"),
            ("pv_kw", "f8"),
            ("buy_tariff_aud_per_kwh", "f8"),
            ("sell_tariff_aud_per_kwh", "f8"),
            ("carbon_intensity_t_per_mwh", "f8"),
        ]
    )
    def get_current_realised_data(self) -> list[dict]:
        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            request = conn.execute(
                "SELECT * FROM simulation_request WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            houses = conn.execute(
                "SELECT house_config_id, quantity FROM simulation_house "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchall()
            if request is None:
                return []
            quantities = {row["house_config_id"]: row["quantity"] for row in houses}
            register_demand_clock(conn, request["location_name"])
            rows = conn.execute(
                """SELECT v.timestamp_utc, d.house_config_id, d.demand_kw, v.pv_kw,
                          p.buy_tariff_aud, p.sell_tariff_aud,
                          e.carbon_intensity_t_per_mwh
                   FROM realised_demand_5m d
                   JOIN realised_pv_5m v
                     ON d.timestamp_utc = demand_time(v.timestamp_utc)
                    AND v.house_config_id = d.house_config_id
                    AND v.location_name = ?
                   JOIN realised_pricing_5m p
                     ON p.timestamp_utc = v.timestamp_utc
                    AND p.location_name = ?
                   JOIN realised_emissions_5m e
                     ON e.timestamp_utc = v.timestamp_utc
                    AND e.location_name = ?
                   WHERE d.house_config_id IN (
                       SELECT house_config_id FROM simulation_house
                       WHERE request_id = ?
                   )
                     AND v.timestamp_utc >= ?
                     AND v.timestamp_utc < ?
                   ORDER BY v.timestamp_utc, d.house_config_id""",
                (
                    request["location_name"],
                    request["location_name"],
                    request["location_name"],
                    request_id,
                    request["start_time_utc"],
                    request["end_time_utc"],
                ),
            ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                row["timestamp_utc"],
                {
                    "timestamp_utc": row["timestamp_utc"],
                    "request_id": request_id,
                    "demand_kw": 0.0,
                    "pv_kw": 0.0,
                    "buy_tariff_aud_per_kwh": row["buy_tariff_aud"],
                    "sell_tariff_aud_per_kwh": row["sell_tariff_aud"],
                    "carbon_intensity_t_per_mwh": row["carbon_intensity_t_per_mwh"],
                },
            )
            quantity = quantities[row["house_config_id"]]
            item["demand_kw"] += row["demand_kw"] * quantity
            item["pv_kw"] += row["pv_kw"] * quantity
        return list(grouped.values())

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("buy_tariff_aud_per_kwh", "f8"),
            ("sell_tariff_aud_per_kwh", "f8"),
        ]
    )
    def get_current_pricing_data(self) -> list[dict]:
        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT timestamp_utc, buy_tariff_aud, sell_tariff_aud
                   FROM realised_pricing_5m
                   WHERE location_name = (
                       SELECT location_name FROM simulation_request
                       WHERE request_id = ?
                   )
                     AND timestamp_utc >= (
                         SELECT start_time_utc FROM simulation_request
                         WHERE request_id = ?
                     )
                     AND timestamp_utc < (
                         SELECT end_time_utc FROM simulation_request
                         WHERE request_id = ?
                     )
                   ORDER BY timestamp_utc""",
                (request_id,) * 3,
            ).fetchall()
        return [
            {
                "timestamp_utc": row["timestamp_utc"],
                "request_id": request_id,
                "buy_tariff_aud_per_kwh": row["buy_tariff_aud"],
                "sell_tariff_aud_per_kwh": row["sell_tariff_aud"],
            }
            for row in rows
        ]

    @register_dtype(
        [
            ("timestamp_utc", "U32"),
            ("request_id", "U16"),
            ("carbon_intensity_t_per_mwh", "f8"),
        ]
    )
    def get_current_emissions_data(self) -> list[dict]:
        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT timestamp_utc, carbon_intensity_t_per_mwh
                   FROM realised_emissions_5m
                   WHERE location_name = (
                       SELECT location_name FROM simulation_request
                       WHERE request_id = ?
                   )
                     AND timestamp_utc >= (
                         SELECT start_time_utc FROM simulation_request
                         WHERE request_id = ?
                     )
                     AND timestamp_utc < (
                         SELECT end_time_utc FROM simulation_request
                         WHERE request_id = ?
                     )
                   ORDER BY timestamp_utc""",
                (request_id, request_id, request_id),
            ).fetchall()
        return [
            {
                "timestamp_utc": row[0],
                "request_id": request_id,
                "carbon_intensity_t_per_mwh": row[1],
            }
            for row in rows
        ]

    @register_dtype(
        [
            ("scenario_name", "U50"),
            ("request_id", "U16"),
            ("battery_degradation_aud", "f8"),
            ("grid_import_aud", "f8"),
            ("grid_export_aud", "f8"),
            ("emissions_tonnes", "f8"),
            ("emissions_cost_aud", "f8"),
            ("realised_economic_cost_aud", "f8"),
            ("comparison_economic_cost_aud", "f8"),
            ("terminal_soc_kwh", "f8"),
            ("estimated_terminal_cost_value_aud", "f8"),
            ("estimated_terminal_emissions_value_tonnes", "f8"),
            ("estimated_terminal_balanced_value_aud", "f8"),
            ("estimated_terminal_balanced_financial_value_aud", "f8"),
            ("estimated_terminal_balanced_emissions_value_tonnes", "f8"),
        ]
    )
    def get_current_operating_costs(self) -> list[dict]:
        request_id = self.current_request_id
        if request_id is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT scenario_name, request_id, battery_degradation_aud,
                          grid_import_aud, grid_export_aud, emissions_tonnes,
                          emissions_cost_aud,
                          grid_import_aud - grid_export_aud
                            + battery_degradation_aud AS realised_economic_cost_aud,
                          grid_import_aud - grid_export_aud
                            + battery_degradation_aud
                            - CASE WHEN scenario_name = 'optimised_battery'
                              THEN COALESCE(estimated_terminal_cost_value_aud, 0)
                              ELSE 0 END AS comparison_economic_cost_aud,
                          terminal_soc_kwh, estimated_terminal_cost_value_aud,
                          estimated_terminal_emissions_value_tonnes
                   FROM simulation_summary
                   WHERE request_id = ?
                   ORDER BY scenario_name""",
                (request_id,),
            ).fetchall()
        costs = [dict(row) for row in rows]
        for row in costs:
            row["estimated_terminal_balanced_value_aud"] = math.nan
            row["estimated_terminal_balanced_financial_value_aud"] = math.nan
            row["estimated_terminal_balanced_emissions_value_tonnes"] = math.nan
            for field in (
                "terminal_soc_kwh",
                "estimated_terminal_cost_value_aud",
                "estimated_terminal_emissions_value_tonnes",
            ):
                if row[field] is None:
                    row[field] = math.nan
        return costs

    # ==========================================
    # WRITES (Must start with "set_")
    # ==========================================

    # Note: dtypes not required for setters, but it enhances readability
    @register_dtype(
        [
            ("location_name", "U50"),
            ("latitude", "f8"),
            ("longitude", "f8"),
            ("timezone", "U50"),
            ("nem_region", "U10"),
        ]
    )
    def set_location(self, location_dict: dict) -> None:
        """Inserts/updates a location in the database."""

        location = Location(**location_dict)

        self.location_dao.save_location(location)
        logger.info("Saved location %s", location.location_name)

    @register_dtype(
        [
            ("house_config_id", "U64"),
            ("pv_peak_capacity", "f8"),
            ("panel_tilt", "f8"),
            ("panel_azimuth", "f8"),
            ("pv_inverter_ac_kw", "f8"),
            ("pv_inverter_efficiency", "f8"),
            ("has_aircon", "?"),
            ("has_poolpump", "?"),
            ("has_ev", "?"),
        ]
    )
    def set_house_config(self, config_dict) -> None:
        """Inserts/updates a house config in the database."""

        config = HouseConfig(**config_dict)

        self.house_dao.save_house_config(config)
        logger.info("Saved house configuration %s", config.house_config_id)

    @register_dtype(
        [
            ("battery_config_id", "U64"),
            ("capacity_kwh", "f8"),
            ("max_power_kw", "f8"),
            ("efficiency", "f8"),
            ("min_soc_frac", "f8"),
            ("max_soc_frac", "f8"),
            ("replacement_cost_aud", "f8"),
            ("lifetime_cycles", "i8"),
            ("max_cycles_24h", "f8"),
        ]
    )
    def set_battery_config(self, config_dict) -> None:
        """Inserts/updates a battery config in the database."""

        config = BatteryConfig(**config_dict)

        self.battery_dao.save_battery_config(config)
        logger.info("Saved battery configuration %s", config.battery_config_id)

    @register_dtype([("request_id", "U16")])
    def set_current_request(self, request_dict) -> None:
        """
        Picks the run the get_current_* getters return. Kept in memory only, not
        saved to the database.
        """

        self.current_request_id = request_dict["request_id"]
        logger.info("Showing run %s", self.current_request_id)

    @register_dtype(
        [
            ("request_id", "U16"),
            ("objective_mode", "U50"),
            ("location_name", "U50"),
            ("start_time_utc", "U32"),
            ("end_time_utc", "U32"),
            ("battery_config_id", "U64"),
            ("houses", "U1024"),
            ("fixed_buy_aud_per_kwh", "f8"),
            ("fixed_sell_aud_per_kwh", "f8"),
        ]
    )
    def set_simulation_request(self, request_dict: dict) -> None:
        request = SimulationRequest(**request_dict)
        SimulationDao(self.db_path, request).save_simulation_request()

    def set_optimiser_output(self, output_dict: dict) -> None:
        request = self._request_for_id(output_dict["request_id"])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO optimiser_output_30m
                   (request_id, timestamp_utc, soc_kwh, charge_kw, discharge_kw,
                    grid_import_kw, grid_export_kw, curtailed_pv_kw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(
                    output_dict[key]
                    for key in (
                        "request_id",
                        "timestamp_utc",
                        "soc_kwh",
                        "charge_kw",
                        "discharge_kw",
                        "grid_import_kw",
                        "grid_export_kw",
                        "curtailed_pv_kw",
                    )
                ),
            )
        _ = request

    def set_live_controller_output(self, output_dict: dict) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO live_controller_output_5m
                   (request_id, scenario_name, timestamp_utc, soc_kwh, charge_kw,
                    discharge_kw, grid_import_kw, grid_export_kw,
                    curtailed_pv_kw, cycle_throughput_kwh, reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(
                    output_dict.get(key, "")
                    for key in (
                        "request_id",
                        "scenario_name",
                        "timestamp_utc",
                        "soc_kwh",
                        "charge_kw",
                        "discharge_kw",
                        "grid_import_kw",
                        "grid_export_kw",
                        "curtailed_pv_kw",
                        "cycle_throughput_kwh",
                        "reasons",
                    )
                ),
            )

    def set_simulation_summary(self, summary_dict: dict) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO simulation_summary
                   (request_id, scenario_name, battery_degradation_aud,
                    grid_import_aud, grid_export_aud, emissions_tonnes,
                    emissions_cost_aud, terminal_soc_kwh,
                    estimated_terminal_cost_value_aud,
                    estimated_terminal_emissions_value_tonnes,
                    terminal_value_horizon_hours)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(
                    summary_dict.get(key)
                    for key in (
                        "request_id",
                        "scenario_name",
                        "battery_degradation_aud",
                        "grid_import_aud",
                        "grid_export_aud",
                        "emissions_tonnes",
                        "emissions_cost_aud",
                        "terminal_soc_kwh",
                        "estimated_terminal_cost_value_aud",
                        "estimated_terminal_emissions_value_tonnes",
                        "terminal_value_horizon_hours",
                    )
                ),
            )

    def _request_for_id(self, request_id: str) -> SimulationRequest:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM simulation_request WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            houses = conn.execute(
                "SELECT house_config_id, quantity FROM simulation_house "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchall()
        if row is None:
            raise ValueError(f"Unknown request_id: {request_id}")
        return SimulationRequest(
            **dict(row),
            houses={house["house_config_id"]: house["quantity"] for house in houses},
        )
