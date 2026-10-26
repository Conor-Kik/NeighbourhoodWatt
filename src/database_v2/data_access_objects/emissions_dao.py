import sqlite3
from datetime import datetime
from pathlib import Path

from backend_v2.aemo.aemo_constants import (
    AEMO_PREDISPATCH_INTERVAL,
    AEMO_PREDISPATCH_PERIODS_PER_DAY,
)
from backend_v2.community_optimiser.optimiser_constants import LIVE_CONTROL_STEP_MINUTES
from database_v2.models.data_models import (
    ForecastEmissionsSnapshot,
    RealisedEmissionsRow,
)


class EmissionsDao:
    """Handles db writes and completeness checks of emissions data."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def has_complete_realised_emissions(
        self,
        start_utc: datetime,
        end_utc: datetime,
        location_name: str,
    ) -> bool:
        expected = int(
            (end_utc - start_utc).total_seconds() / (60 * LIVE_CONTROL_STEP_MINUTES)
        )
        query = """
        SELECT COUNT(*) FROM realised_emissions_5m
        WHERE location_name = ? AND timestamp_utc >= ? AND timestamp_utc < ?"""
        count = self.conn.execute(
            query, (location_name, start_utc.isoformat(), end_utc.isoformat())
        ).fetchone()[0]
        return count >= expected

    def has_complete_forecast_window(
        self,
        start_utc: datetime,
        end_utc: datetime,
        location_name: str,
    ) -> bool:
        periods = int((end_utc - start_utc) / AEMO_PREDISPATCH_INTERVAL)
        rows = self.conn.execute(
            """SELECT run_time_utc, target_time_utc FROM forecast_emissions_30m
            WHERE location_name = ? AND run_time_utc >= ? AND run_time_utc < ?""",
            (
                location_name,
                start_utc.isoformat(),
                end_utc.isoformat(),
            ),
        ).fetchall()
        expected = {
            (
                (start_utc + run * AEMO_PREDISPATCH_INTERVAL).isoformat(),
                (start_utc + (run + lead) * AEMO_PREDISPATCH_INTERVAL).isoformat(),
            )
            for run in range(periods)
            for lead in range(AEMO_PREDISPATCH_PERIODS_PER_DAY)
        }
        complete = {(row[0], row[1]) for row in rows} == expected
        return complete

    def save_realised_emissions(self, records: list[RealisedEmissionsRow]) -> None:
        query = """
        INSERT OR REPLACE INTO realised_emissions_5m
        (timestamp_utc, location_name, carbon_intensity_t_per_mwh)
        VALUES (?, ?, ?)
        """
        rows = [
            (r.timestamp_utc.isoformat(), r.location_name, r.carbon_intensity_t_per_mwh)
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def save_forecast_emissions(
        self,
        records: list[ForecastEmissionsSnapshot],
    ) -> None:
        query = """
        INSERT OR REPLACE INTO forecast_emissions_30m
        (run_time_utc, target_time_utc, location_name, carbon_intensity_t_per_mwh)
        VALUES (?, ?, ?, ?)
        """
        rows = [
            (
                record.run_time_utc.isoformat(),
                target_time.isoformat(),
                record.location_name,
                carbon_intensity,
            )
            for record in records
            for target_time, carbon_intensity in zip(
                record.target_times_utc,
                record.carbon_intensities_t_per_mwh,
                strict=True,
            )
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()
