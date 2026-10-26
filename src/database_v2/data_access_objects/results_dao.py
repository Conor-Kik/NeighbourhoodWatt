import sqlite3
from pathlib import Path
from typing import Any

from backend_v2.community_optimiser.optimiser_constants import LIVE_CONTROL_STEP_MINUTES
from database_v2.data_access_objects.demand_dao import register_demand_clock
from database_v2.models.request_models import BatteryConfig, SimulationRequest


class ResultsDao:
    """
    Calculates and saves final summary KPIs for the simulation directly using SQL.
    Executed once at the very end of the simulation run.
    """

    def __init__(self, db_path: str | Path, request: SimulationRequest):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.request = request

    def calculate_and_save_all(self) -> None:
        """Orchestrates the calculations and saves them to the summary table."""
        self._save_summaries(
            [
                self._calc_battery_summary("optimised_battery"),
                self._calc_battery_summary("perfect_foresight"),
                self._calc_battery_summary("reactive_battery"),
                *self.get_no_battery_summaries(),
            ]
        )

    def get_no_battery_summaries(self) -> list[dict[str, Any]]:
        """Calculate both no-battery scenarios using the realised run data."""
        register_demand_clock(self.conn, self.request.location_name)
        return [self._calc_no_battery_shared(), self._calc_no_battery_individual()]

    def close(self) -> None:
        self.conn.close()

    def get_summaries(self) -> list[dict[str, Any]]:
        """Expose measured totals and terminal-credit comparison metrics together."""
        query = """
            SELECT *,
                grid_import_aud - grid_export_aud + battery_degradation_aud
                    AS realised_economic_cost_aud,
                grid_import_aud - grid_export_aud + battery_degradation_aud
                    - CASE WHEN scenario_name = 'optimised_battery'
                        THEN COALESCE(estimated_terminal_cost_value_aud, 0)
                        ELSE 0 END AS comparison_economic_cost_aud,
                emissions_tonnes
                    - CASE WHEN scenario_name = 'optimised_battery'
                        THEN COALESCE(estimated_terminal_emissions_value_tonnes, 0)
                        ELSE 0 END AS comparison_emissions_tonnes
            FROM simulation_summary
            WHERE request_id = ?
            ORDER BY scenario_name
        """
        return [
            dict(row) for row in self.conn.execute(query, (self.request.request_id,))
        ]

    def save_terminal_value(
        self,
        scenario: str,
        soc_kwh: float,
        cost_value_aud: float,
        emissions_value_tonnes: float,
        horizon_hours: float,
    ) -> None:
        """Store forecast estimates separately from measured costs and emissions."""
        self.conn.execute(
            """UPDATE simulation_summary
               SET terminal_soc_kwh = ?, estimated_terminal_cost_value_aud = ?,
                   estimated_terminal_emissions_value_tonnes = ?,
                   terminal_value_horizon_hours = ?
               WHERE request_id = ? AND scenario_name = ?""",
            (
                soc_kwh,
                cost_value_aud,
                emissions_value_tonnes,
                horizon_hours,
                self.request.request_id,
                scenario,
            ),
        )

    def _calc_battery_summary(self, scenario_name: str) -> dict[str, Any]:
        """Calculates totals directly from the 5-minute controller outputs in db."""
        register_demand_clock(self.conn, self.request.location_name)

        interval_hours = LIVE_CONTROL_STEP_MINUTES / 60

        # fetch grid import/export/emissions costs.
        query = """
        SELECT
            COUNT(*) AS num_intervals,
            SUM(o.cycle_throughput_kwh * ?) AS battery_degradation_aud,
            SUM(o.grid_import_kw * ? * p.buy_tariff_aud)
                AS grid_import_aud,
            SUM(o.grid_export_kw * ? * p.sell_tariff_aud)
                AS grid_export_aud,
            SUM(o.grid_import_kw * ? * e.carbon_intensity_t_per_mwh / 1000)
                AS total_emissions_tonnes,
            SUM(o.grid_import_kw * ? * e.carbon_intensity_t_per_mwh
                / 1000 * cp.carbon_price_aud_per_t) AS emissions_cost_aud
        FROM live_controller_output_5m o
        JOIN realised_pricing_5m p ON o.timestamp_utc = p.timestamp_utc
            AND p.location_name = ?
        JOIN realised_emissions_5m e ON o.timestamp_utc = e.timestamp_utc
            AND e.location_name = ?
        JOIN carbon_price cp ON cp.calendar_year =
            CAST(substr(demand_time(o.timestamp_utc), 1, 4) AS INTEGER)
        WHERE o.scenario_name = ? AND o.request_id = ?
        """

        battery_row = self.conn.execute(
            "SELECT * FROM battery_config WHERE battery_config_id = ?",
            (self.request.battery_config_id,),
        ).fetchone()
        degradation = BatteryConfig(**dict(battery_row)).degradation_aud_per_kwh

        row = self.conn.execute(
            query,
            (
                degradation,
                interval_hours,
                interval_hours,
                interval_hours,
                interval_hours,
                self.request.location_name,
                self.request.location_name,
                scenario_name,
                self.request.request_id,
            ),
        ).fetchone()

        expected_intervals = int(
            (self.request.end_time_utc - self.request.start_time_utc).total_seconds()
            / (LIVE_CONTROL_STEP_MINUTES * 60)
        )
        if row["num_intervals"] != expected_intervals:
            raise RuntimeError(
                f"Expected {expected_intervals} {scenario_name} intervals, "
                f"found {row['num_intervals']}"
            )

        return {
            "request_id": self.request.request_id,
            "scenario_name": scenario_name,
            "battery_degradation_aud": row["battery_degradation_aud"],
            "grid_import_aud": row["grid_import_aud"],
            "grid_export_aud": row["grid_export_aud"],
            "emissions_tonnes": row["total_emissions_tonnes"],
            "emissions_cost_aud": row["emissions_cost_aud"],
        }

    def _calc_no_battery_shared(self) -> dict[str, Any]:
        """Calculates baseline by aggregating community power first."""

        scenario_name = "no_battery_shared"
        interval_hours = LIVE_CONTROL_STEP_MINUTES / 60

        # fetch grid import/export/emissions costs.
        query = """
        WITH community AS (
            SELECT
                p.timestamp_utc,
                SUM((d.demand_kw - p.pv_kw) * sh.quantity) AS net_kw
            FROM simulation_house sh
            JOIN realised_demand_5m d ON sh.house_config_id = d.house_config_id
            JOIN realised_pv_5m p ON d.timestamp_utc = demand_time(p.timestamp_utc)
                AND d.house_config_id = p.house_config_id
                AND p.location_name = ?
            WHERE sh.request_id = ?
                AND p.timestamp_utc >= ? AND p.timestamp_utc < ?
            GROUP BY p.timestamp_utc
        )
        SELECT
            COUNT(*) AS num_intervals,
            SUM(MAX(c.net_kw, 0) * ? * COALESCE(?, p.buy_tariff_aud))
                AS grid_import_aud,
            SUM(MAX(-c.net_kw, 0) * ? * COALESCE(?, p.sell_tariff_aud))
                AS grid_export_aud,
            SUM(MAX(c.net_kw, 0) * ? * e.carbon_intensity_t_per_mwh / 1000)
                AS total_emissions_tonnes,
            SUM(MAX(c.net_kw, 0) * ? * e.carbon_intensity_t_per_mwh
                / 1000 * cp.carbon_price_aud_per_t) AS emissions_cost_aud
        FROM community c
        JOIN realised_pricing_5m p ON c.timestamp_utc = p.timestamp_utc
            AND p.location_name = ?
        JOIN realised_emissions_5m e ON c.timestamp_utc = e.timestamp_utc
            AND e.location_name = ?
        JOIN carbon_price cp ON cp.calendar_year =
            CAST(substr(demand_time(c.timestamp_utc), 1, 4) AS INTEGER)
        """

        row = self.conn.execute(
            query,
            (
                self.request.location_name,
                self.request.request_id,
                self.request.start_time_utc.isoformat(),
                self.request.end_time_utc.isoformat(),
                interval_hours,
                self.request.fixed_buy_aud_per_kwh,
                interval_hours,
                self.request.fixed_sell_aud_per_kwh,
                interval_hours,
                interval_hours,
                self.request.location_name,
                self.request.location_name,
            ),
        ).fetchone()

        expected_intervals = int(
            (self.request.end_time_utc - self.request.start_time_utc).total_seconds()
            / (LIVE_CONTROL_STEP_MINUTES * 60)
        )
        if row["num_intervals"] != expected_intervals:
            raise RuntimeError(
                f"Expected {expected_intervals} {scenario_name} intervals, "
                f"found {row['num_intervals']}"
            )

        return {
            "request_id": self.request.request_id,
            "scenario_name": scenario_name,
            "battery_degradation_aud": 0.0,
            "grid_import_aud": row["grid_import_aud"],
            "grid_export_aud": row["grid_export_aud"],
            "emissions_tonnes": row["total_emissions_tonnes"],
            "emissions_cost_aud": row["emissions_cost_aud"],
        }

    def _calc_no_battery_individual(self) -> dict[str, Any]:
        """Calculates baseline by applying tariffs to each house individually."""
        scenario_name = "no_battery_individual"
        interval_hours = LIVE_CONTROL_STEP_MINUTES / 60

        # fetch grid import/export/emissions costs.
        query = """
        WITH energy_per_config AS (
            SELECT
                p.timestamp_utc,
                d.house_config_id,
                sh.quantity,
                d.demand_kw - p.pv_kw AS net_kw
            FROM simulation_house sh
            JOIN realised_demand_5m d ON sh.house_config_id = d.house_config_id
            JOIN realised_pv_5m p ON d.timestamp_utc = demand_time(p.timestamp_utc)
                AND d.house_config_id = p.house_config_id
                AND p.location_name = ?
            WHERE sh.request_id = ?
                AND p.timestamp_utc >= ? AND p.timestamp_utc < ?
        ),
        cost_per_config AS (
            SELECT
                h.house_config_id,
                SUM(MAX(h.net_kw, 0) * h.quantity * ?
                    * COALESCE(?, p.buy_tariff_aud)) AS house_import_aud,
                SUM(MAX(-h.net_kw, 0) * h.quantity * ?
                    * COALESCE(?, p.sell_tariff_aud)) AS house_export_aud,
                SUM(MAX(h.net_kw, 0) * h.quantity * ?
                    * e.carbon_intensity_t_per_mwh / 1000)
                    AS house_emissions_tonnes,
                SUM(MAX(h.net_kw, 0) * h.quantity * ?
                    * e.carbon_intensity_t_per_mwh / 1000
                    * cp.carbon_price_aud_per_t) AS house_emissions_cost_aud,
                COUNT(*) AS num_intervals
            FROM energy_per_config h
            JOIN realised_pricing_5m p ON h.timestamp_utc = p.timestamp_utc
                AND p.location_name = ?
            JOIN realised_emissions_5m e ON h.timestamp_utc = e.timestamp_utc
                AND e.location_name = ?
            JOIN carbon_price cp ON cp.calendar_year =
                CAST(substr(demand_time(h.timestamp_utc), 1, 4) AS INTEGER)
            GROUP BY h.house_config_id
        )
        SELECT
            num_intervals,
            SUM(x.house_import_aud) AS grid_import_aud,
            SUM(x.house_export_aud) AS grid_export_aud,
            SUM(x.house_emissions_tonnes) AS total_emissions_tonnes,
            SUM(x.house_emissions_cost_aud) AS emissions_cost_aud
        FROM cost_per_config x
        """

        row = self.conn.execute(
            query,
            (
                self.request.location_name,
                self.request.request_id,
                self.request.start_time_utc.isoformat(),
                self.request.end_time_utc.isoformat(),
                interval_hours,
                self.request.fixed_buy_aud_per_kwh,
                interval_hours,
                self.request.fixed_sell_aud_per_kwh,
                interval_hours,
                interval_hours,
                self.request.location_name,
                self.request.location_name,
            ),
        ).fetchone()

        expected_intervals = int(
            (self.request.end_time_utc - self.request.start_time_utc).total_seconds()
            / (LIVE_CONTROL_STEP_MINUTES * 60)
        )
        if row["num_intervals"] != expected_intervals:
            raise RuntimeError(
                f"Expected {expected_intervals} {scenario_name} intervals, "
                f"found {row['num_intervals']}"
            )

        return {
            "request_id": self.request.request_id,
            "scenario_name": scenario_name,
            "battery_degradation_aud": 0.0,
            "grid_import_aud": row["grid_import_aud"],
            "grid_export_aud": row["grid_export_aud"],
            "emissions_tonnes": row["total_emissions_tonnes"],
            "emissions_cost_aud": row["emissions_cost_aud"],
        }

    def _save_summaries(self, summaries: list[dict[str, Any]]) -> None:
        """Bulk inserts into the summary table."""
        query = """
            INSERT INTO simulation_summary (
                request_id, scenario_name, battery_degradation_aud,
                grid_import_aud, grid_export_aud, emissions_tonnes, emissions_cost_aud
            )
            VALUES (
                :request_id, :scenario_name, :battery_degradation_aud,
                :grid_import_aud, :grid_export_aud, :emissions_tonnes,
                :emissions_cost_aud
            )
        """
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, summaries)
        self.conn.execute("COMMIT")
