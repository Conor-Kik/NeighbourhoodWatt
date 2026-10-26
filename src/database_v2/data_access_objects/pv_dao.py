import sqlite3
from datetime import datetime
from pathlib import Path

from backend_v2.community_optimiser.optimiser_constants import LIVE_CONTROL_STEP_MINUTES
from backend_v2.generation.pv_forecast_schedule import (
    FORECAST_STEP,
    pv_cycle_windows,
)
from database_v2.models.data_models import (
    ForecastPvSnapshot,
    RealisedPvRow,
)


class PvDao:
    """Handles db writes and completeness checks of household PV data."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def has_complete_realised_pv(
        self,
        start_utc: datetime,
        end_utc: datetime,
        location_name: str,
        house_config_ids: list[str],
    ) -> bool:
        expected = int(
            (end_utc - start_utc).total_seconds() / (60 * LIVE_CONTROL_STEP_MINUTES)
        ) * len(house_config_ids)

        placeholders = ",".join("?" * len(house_config_ids))

        query = f"""
            SELECT COUNT(*) FROM realised_pv_5m
            WHERE house_config_id IN ({placeholders})
            AND timestamp_utc >= ? AND timestamp_utc < ?
            AND location_name = ?"""

        count = self.conn.execute(
            query,
            (
                *house_config_ids,
                start_utc.isoformat(),
                end_utc.isoformat(),
                location_name,
            ),
        ).fetchone()[0]

        return count >= expected

    def has_forecast_pv_cycle(
        self,
        available_at_utc: datetime,
        location_name: str,
        house_config_ids: list[str],
    ) -> bool:
        placeholders = ",".join("?" * len(house_config_ids))
        query = f"""
            SELECT COUNT(DISTINCT house_config_id) FROM forecast_pv_cycle_30m
            WHERE house_config_id IN ({placeholders}) AND available_at_utc = ?
            AND location_name = ?"""

        count = self.conn.execute(
            query, (*house_config_ids, available_at_utc.isoformat(), location_name)
        ).fetchone()[0]

        return count >= len(house_config_ids)

    def has_complete_forecast_pv_window(
        self,
        start_utc: datetime,
        end_utc: datetime,
        location_name: str,
        house_config_ids: list[str],
    ) -> bool:
        """Check each needed daily cycle without expanding rolling windows."""
        placeholders = ",".join("?" * len(house_config_ids))
        for available_at, (first_target, end_target) in pv_cycle_windows(
            start_utc, end_utc
        ).items():
            expected = {
                (first_target + lead * FORECAST_STEP).isoformat()
                for lead in range(int((end_target - first_target) / FORECAST_STEP))
            }
            rows = self.conn.execute(
                f"""SELECT target_time_utc, house_config_id
                FROM forecast_pv_cycle_30m
                WHERE available_at_utc = ? AND house_config_id IN ({placeholders})
                AND target_time_utc >= ? AND target_time_utc < ?
                AND location_name = ?""",
                (
                    available_at.isoformat(),
                    *house_config_ids,
                    first_target.isoformat(),
                    end_target.isoformat(),
                    location_name,
                ),
            ).fetchall()
            if len(rows) != len(expected) * len(house_config_ids):
                return False
            if any(row["target_time_utc"] not in expected for row in rows):
                return False
        return True

    def save_realised_pv(self, records: list[RealisedPvRow]) -> None:
        query = """
            INSERT INTO realised_pv_5m
            (timestamp_utc, house_config_id, pv_kw, location_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(timestamp_utc, house_config_id, location_name) DO UPDATE SET
                pv_kw = excluded.pv_kw
        """

        rows = [
            (r.timestamp_utc.isoformat(), r.house_config_id, r.pv_kw, r.location_name)
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany(query, rows)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def save_forecast_pv(self, records: list[ForecastPvSnapshot]) -> None:
        query = """
            INSERT INTO forecast_pv_cycle_30m
            (available_at_utc, target_time_utc, house_config_id, pv_kw, location_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(available_at_utc, target_time_utc,
            house_config_id, location_name)
            DO UPDATE SET pv_kw = excluded.pv_kw
        """

        rows = (
            (
                record.available_at_utc.isoformat(),
                target_time.isoformat(),
                record.house_config_id,
                pv_kw,
                record.location_name,
            )
            for record in records
            for target_time, pv_kw in zip(
                record.target_times_utc,
                record.pv_kw,
                strict=True,
            )
        )
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany(query, rows)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()
