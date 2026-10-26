import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    OPTIMISER_PERIOD_HOURS,
    PERIODS_PER_DAY,
)
from backend_v2.generation.pv_forecast_schedule import available_pv_cycle_at
from database_v2.data_access_objects.demand_dao import register_demand_clock
from database_v2.models.optimiser_models import (
    ForecastWindow,
    LiveControllerOutput,
    LiveStep,
    OptimiserInput,
    OptimiserOutput,
    RealisedWindows,
)
from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class SimulationDao:
    """Handles simulation-related SQL operations."""

    def __init__(self, db_path: str | Path, request: SimulationRequest):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        # Needed for ON DELETE CASCADE when clearing an incomplete run.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.request = request
        register_demand_clock(self.conn, request.location_name)

    def run_exists(self) -> bool:
        """Checks if this exact simulation already exists.
        Incomplete runs do not count, they will be overwritten."""
        expected_periods = int(
            (self.request.end_time_utc - self.request.start_time_utc)
            / timedelta(hours=OPTIMISER_PERIOD_HOURS)
        )
        query = "SELECT COUNT(*) FROM optimiser_output_30m WHERE request_id = ?"
        count = self.conn.execute(query, (self.request.request_id,)).fetchone()[0]
        summary_query = """
            SELECT COUNT(*) FROM simulation_summary
            WHERE request_id = ?
                AND scenario_name IN (
                    'optimised_battery', 'perfect_foresight', 'reactive_battery'
                )
                AND (
                    scenario_name != 'optimised_battery'
                    OR (estimated_terminal_cost_value_aud IS NOT NULL
                        AND estimated_terminal_emissions_value_tonnes IS NOT NULL)
                )
        """
        summary_count = self.conn.execute(
            summary_query,
            (self.request.request_id,),
        ).fetchone()[0]
        return count == expected_periods and summary_count == 3

    def get_forecast_window(self, run_time_utc: datetime) -> ForecastWindow:
        """Fetches the aggregated data for one 30m optimiser step."""

        run_time_iso = run_time_utc.isoformat()
        end_time_iso = (
            run_time_utc + timedelta(hours=0.5 * PERIODS_PER_DAY)
        ).isoformat()

        carbon_price_row = self.conn.execute(
            """SELECT carbon_price_aud_per_t
                FROM carbon_price
                WHERE calendar_year =
                    CAST(substr(demand_time(?), 1, 4) AS INTEGER)""",
            (run_time_iso,),
        ).fetchone()
        if carbon_price_row is None:
            message = (
                f"No carbon price configured for {self.request.location_name} "
                f"at {run_time_iso}"
            )
            logger.error(message)
            raise RuntimeError(message)
        carbon_price = carbon_price_row[0]

        price_rows = self.conn.execute(
            """SELECT target_time_utc, buy_tariff_aud, sell_tariff_aud
                FROM forecast_pricing_30m
                WHERE location_name = ? AND run_time_utc = ?
                    AND target_time_utc >= ? AND target_time_utc < ?
                ORDER BY target_time_utc ASC""",
            (
                self.request.location_name,
                run_time_iso,
                run_time_iso,
                end_time_iso,
            ),
        ).fetchall()

        emissions_rows = self.conn.execute(
            """SELECT target_time_utc, carbon_intensity_t_per_mwh
                FROM forecast_emissions_30m
                WHERE location_name = ? AND run_time_utc = ?
                    AND target_time_utc >= ? AND target_time_utc < ?
                ORDER BY target_time_utc ASC""",
            (
                self.request.location_name,
                run_time_iso,
                run_time_iso,
                end_time_iso,
            ),
        ).fetchall()

        # houses is dict[house_config_id -> quantity]
        house_config_ids = list(self.request.houses)
        quantities = self.request.houses
        placeholders = ",".join("?" * len(house_config_ids))

        # Fetch per-config demand rows then scale by quantity to get community total.
        raw_demand_rows = self.conn.execute(
            f"""SELECT p.target_time_utc, d.house_config_id, d.demand_kw
                FROM forecast_pricing_30m p
                JOIN forecast_demand_30m d
                    ON d.target_time_utc = demand_time(p.target_time_utc)
                    AND d.run_time_utc =
                        substr(demand_time(p.target_time_utc), 1, 10)
                        || 'T00:00:00+00:00'
                    AND d.house_config_id IN ({placeholders})
                WHERE p.location_name = ? AND p.run_time_utc = ?
                    AND p.target_time_utc >= ? AND p.target_time_utc < ?
                ORDER BY p.target_time_utc""",
            [
                *house_config_ids,
                self.request.location_name,
                run_time_iso,
                run_time_iso,
                end_time_iso,
            ],
        ).fetchall()

        demand_by_target: dict[str, float] = {}
        for row in raw_demand_rows:
            t = row["target_time_utc"]
            qty = quantities[row["house_config_id"]]
            demand_by_target[t] = demand_by_target.get(t, 0.0) + row["demand_kw"] * qty

        available_at = available_pv_cycle_at(run_time_utc)

        # Fetch per-config PV rows then scale by quantity to get community total.
        raw_pv_rows = self.conn.execute(
            f"""SELECT target_time_utc, house_config_id, pv_kw
                FROM forecast_pv_cycle_30m
                WHERE available_at_utc = ?
                    AND target_time_utc >= ? AND target_time_utc < ?
                    AND house_config_id IN ({placeholders})
                    AND location_name = ?
                ORDER BY target_time_utc""",
            [
                available_at.isoformat(),
                run_time_iso,
                end_time_iso,
                *house_config_ids,
                self.request.location_name,
            ],
        ).fetchall()

        pv_by_target: dict[str, float] = {}
        for row in raw_pv_rows:
            t = row["target_time_utc"]
            qty = quantities[row["house_config_id"]]
            pv_by_target[t] = pv_by_target.get(t, 0.0) + row["pv_kw"] * qty

        targets = [row["target_time_utc"] for row in price_rows]
        if any(
            target not in demand_by_target or target not in pv_by_target
            for target in targets
        ):
            raise RuntimeError("Forecast demand or PV does not cover the price window")
        logger.debug(
            "Forecast %s: %d price, %d emissions, %d demand/PV periods",
            run_time_iso,
            len(price_rows),
            len(emissions_rows),
            len(demand_by_target),
        )

        return ForecastWindow(
            run_time_utc=run_time_utc,
            carbon_price_aud_per_t=carbon_price,
            target_times_utc=[r["target_time_utc"] for r in price_rows],
            demand_kw=[demand_by_target[target] for target in targets],
            pv_kw=[pv_by_target[target] for target in targets],
            buy_tariff_aud_per_kwh=[r["buy_tariff_aud"] for r in price_rows],
            sell_tariff_aud_per_kwh=[r["sell_tariff_aud"] for r in price_rows],
            carbon_intensity_t_per_mwh=[
                r["carbon_intensity_t_per_mwh"] for r in emissions_rows
            ],
        )

    def get_realised_windows(self, run_time_utc: datetime) -> RealisedWindows:
        """Fetches the aggregated 5-min realised data for a 30-minute step."""

        end_utc = run_time_utc + timedelta(minutes=30)
        house_config_ids = list(self.request.houses)
        quantities = self.request.houses
        placeholders = ",".join("?" * len(house_config_ids))

        # Fetch per-config demand and PV rows so each can be scaled by quantity.
        query = f"""
            SELECT
                p.timestamp_utc,
                d.house_config_id,
                d.demand_kw,
                p.pv_kw,
                pr.buy_tariff_aud,
                pr.sell_tariff_aud,
                e.carbon_intensity_t_per_mwh
            FROM realised_demand_5m d
            JOIN realised_pv_5m p
                ON d.timestamp_utc = demand_time(p.timestamp_utc)
                AND d.house_config_id = p.house_config_id
                AND p.location_name = ?
            JOIN realised_pricing_5m pr
                ON p.timestamp_utc = pr.timestamp_utc
                AND pr.location_name = ?
            JOIN realised_emissions_5m e
                ON p.timestamp_utc = e.timestamp_utc
                AND e.location_name = ?
            WHERE p.timestamp_utc >= ? AND p.timestamp_utc < ?
                AND d.house_config_id IN ({placeholders})
            ORDER BY p.timestamp_utc ASC, d.house_config_id ASC
        """

        params = [
            self.request.location_name,
            self.request.location_name,
            self.request.location_name,
            run_time_utc.isoformat(),
            end_utc.isoformat(),
            *house_config_ids,
        ]
        rows = self.conn.execute(query, params).fetchall()

        # Aggregate across house configs, scaling each by its quantity.
        agg: dict[str, dict] = {}
        for row in rows:
            t = row["timestamp_utc"]
            qty = quantities[row["house_config_id"]]
            if t not in agg:
                agg[t] = {
                    "agg_demand": 0.0,
                    "agg_pv": 0.0,
                    "buy_tariff_aud": row["buy_tariff_aud"],
                    "sell_tariff_aud": row["sell_tariff_aud"],
                    "carbon_intensity_t_per_mwh": row["carbon_intensity_t_per_mwh"],
                }
            agg[t]["agg_demand"] += row["demand_kw"] * qty
            agg[t]["agg_pv"] += row["pv_kw"] * qty

        sorted_times = sorted(agg)
        logger.debug(
            "Realised window %s: %d joined intervals", run_time_utc, len(sorted_times)
        )

        return RealisedWindows(
            run_times_utc=[datetime.fromisoformat(t) for t in sorted_times],
            agg_demand_kw=[agg[t]["agg_demand"] for t in sorted_times],
            agg_pv_kw=[agg[t]["agg_pv"] for t in sorted_times],
            buy_tariff_aud_per_kwh=[agg[t]["buy_tariff_aud"] for t in sorted_times],
            sell_tariff_aud_per_kwh=[agg[t]["sell_tariff_aud"] for t in sorted_times],
            carbon_intensity_t_per_mwh=[
                agg[t]["carbon_intensity_t_per_mwh"] for t in sorted_times
            ],
        )

    def save_simulation_request(self) -> None:
        """Saves the simulation request. Will replace any existing request.
        Caller should check if run exists first, and decide whether to overwrite."""

        request_id = self.request.request_id

        # Utilising transaction due to wanting to ensure all or nothing.
        self.conn.execute("BEGIN TRANSACTION")

        # Cascades to simulation_house and all output tables.
        self.conn.execute(
            "DELETE FROM simulation_request WHERE request_id = ?", (request_id,)
        )

        self.conn.execute(
            """INSERT INTO simulation_request (
                request_id, objective_mode, location_name,
                start_time_utc, end_time_utc, battery_config_id,
                fixed_buy_aud_per_kwh, fixed_sell_aud_per_kwh
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                self.request.objective_mode.value,
                self.request.location_name,
                self.request.start_time_utc.isoformat(),
                self.request.end_time_utc.isoformat(),
                self.request.battery_config_id,
                self.request.fixed_buy_aud_per_kwh,
                self.request.fixed_sell_aud_per_kwh,
            ),
        )
        self.conn.executemany(
            "INSERT INTO simulation_house (request_id, house_config_id, quantity) "
            "VALUES (?, ?, ?)",
            [(request_id, cfg_id, qty) for cfg_id, qty in self.request.houses.items()],
        )

        self.conn.execute("COMMIT TRANSACTION")

    def save_optimiser_output(
        self, timestamp_utc: datetime, input: OptimiserOutput
    ) -> None:
        """Inserts a single optimiser output."""
        query = """INSERT INTO optimiser_output_30m (
                request_id, timestamp_utc, soc_kwh, charge_kw, discharge_kw,
                grid_import_kw, grid_export_kw, curtailed_pv_kw
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""

        self.conn.execute(
            query,
            (
                self.request.request_id,
                timestamp_utc.isoformat(),
                input.soc_kwh,
                input.charge_kw,
                input.discharge_kw,
                input.grid_import_kw,
                input.grid_export_kw,
                input.curtailed_pv_kw,
            ),
        )

    def save_optimiser_outputs(self, outputs: list[OptimiserOutput]) -> None:
        """Bulk inserts optimiser outputs from start_time_utc."""
        for i, output in enumerate(outputs):
            timestamp_utc = self.request.start_time_utc + timedelta(minutes=30 * i)
            self.save_optimiser_output(timestamp_utc, output)

    def save_live_controller_output(
        self,
        scenario_name: str,  # This will be 'optimised_battery' or 'reactive_battery'
        timestamp_utc: datetime,
        output: LiveControllerOutput,
    ) -> None:
        """Inserts a single live controller output."""
        query = """INSERT INTO live_controller_output_5m (
                request_id, scenario_name, timestamp_utc, soc_kwh,
                charge_kw, discharge_kw, grid_import_kw, grid_export_kw,
                curtailed_pv_kw, cycle_throughput_kwh, reasons
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        self.conn.execute(
            query,
            (
                self.request.request_id,
                scenario_name,
                timestamp_utc.isoformat(),
                output.next_soc_kwh,
                output.charge_kw,
                output.discharge_kw,
                output.grid_import_kw,
                output.grid_export_kw,
                output.curtailed_pv_kw,
                output.cycle_throughput_kwh,
                ",".join(output.reasons),
            ),
        )

    def save_live_controller_outputs(
        self,
        scenario_name: str,  # This will be 'optimised_battery' or 'reactive_battery'
        outputs: list[LiveStep],
    ) -> None:
        """Bulk inserts live controller outputs from a list of LiveStep objects."""
        query = """
            INSERT INTO live_controller_output_5m (
                request_id, scenario_name, timestamp_utc, soc_kwh,
                charge_kw, discharge_kw, grid_import_kw, grid_export_kw,
                curtailed_pv_kw, cycle_throughput_kwh, reasons
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        rows = [
            (
                self.request.request_id,
                scenario_name,
                step.timestamp_utc.isoformat(),
                step.output.next_soc_kwh,
                step.output.charge_kw,
                step.output.discharge_kw,
                step.output.grid_import_kw,
                step.output.grid_export_kw,
                step.output.curtailed_pv_kw,
                step.output.cycle_throughput_kwh,
                ",".join(step.output.reasons),
            )
            for step in outputs
        ]

        # All or nothing
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT TRANSACTION")

    def save_perfect_foresight(
        self, data: OptimiserInput, decisions: list[OptimiserOutput]
    ) -> None:
        """Store the realised optimum in the existing five-minute output table."""
        expected = int(
            (self.request.end_time_utc - self.request.start_time_utc)
            / timedelta(minutes=5)
        )
        if len(decisions) != expected or data.period_hours != 5 / 60:
            raise ValueError("Perfect foresight must cover every five-minute period")
        if any(d.next_soc_kwh is None for d in decisions):
            raise ValueError("Perfect foresight decisions must include next SOC")
        efficiency = data.battery_constants.efficiency
        rows = [
            (
                self.request.request_id,
                "perfect_foresight",
                (self.request.start_time_utc + timedelta(minutes=5 * i)).isoformat(),
                d.next_soc_kwh,
                d.charge_kw,
                d.discharge_kw,
                d.grid_import_kw,
                d.grid_export_kw,
                d.curtailed_pv_kw,
                (efficiency * d.charge_kw + d.discharge_kw / efficiency)
                * data.period_hours,
                "",
            )
            for i, d in enumerate(decisions)
        ]
        query = """
            INSERT INTO live_controller_output_5m (
                request_id, scenario_name, timestamp_utc, soc_kwh,
                charge_kw, discharge_kw, grid_import_kw, grid_export_kw,
                curtailed_pv_kw, cycle_throughput_kwh, reasons
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany(query, rows)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self):
        self.conn.close()

    def get_terminal_throughput_history(self) -> list[float]:
        """Rebuild the engine's final 48 half-hour cycle-budget samples."""
        end = self.request.end_time_utc
        start = max(self.request.start_time_utc, end - timedelta(days=1))
        rows = self.conn.execute(
            """SELECT CAST((unixepoch(:end) - unixepoch(timestamp_utc) - 1)
                           / :period_seconds AS INTEGER) AS age,
                      SUM(cycle_throughput_kwh) AS throughput, COUNT(*) AS steps
               FROM live_controller_output_5m
               WHERE request_id = :request_id AND scenario_name = 'optimised_battery'
                 AND timestamp_utc >= :start AND timestamp_utc < :end
               GROUP BY age""",
            {
                "end": end.isoformat(),
                "start": start.isoformat(),
                "request_id": self.request.request_id,
                "period_seconds": OPTIMISER_PERIOD_HOURS * 3600,
            },
        ).fetchall()
        expected = (end - start).total_seconds() / (LIVE_CONTROL_STEP_MINUTES * 60)
        if sum(row["steps"] for row in rows) != expected:
            raise RuntimeError("Terminal cycle history is incomplete")
        history = [0.0] * PERIODS_PER_DAY
        for row in rows:
            history[PERIODS_PER_DAY - 1 - row["age"]] = row["throughput"]
        return history
