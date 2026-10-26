import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from database_v2.models.data_models import (
    ForecastDemandRow,
    RealisedDemandRow,
)


def demand_clock_time(timestamp_utc: datetime, zone: ZoneInfo) -> datetime:
    """Map actual UTC to a shared profile's local clock bin, including DST."""
    if timestamp_utc.tzinfo is None:
        timestamp_utc = timestamp_utc.replace(tzinfo=UTC)
    return timestamp_utc.astimezone(zone).replace(tzinfo=UTC)


def register_demand_clock(conn: sqlite3.Connection, location_name: str) -> None:
    """Map UTC demand joins to the location's local profile clock."""
    location = conn.execute(
        "SELECT timezone FROM location WHERE location_name = ?", (location_name,)
    ).fetchone()
    if location is None:
        raise ValueError(f"Unknown demand location: {location_name}")
    zone = ZoneInfo(location[0])

    def profile_time(stamp: str) -> str:
        return demand_clock_time(datetime.fromisoformat(stamp), zone).isoformat()

    conn.create_function("demand_time", 1, profile_time, deterministic=True)


class DemandDao:
    """Handles db writes and completeness checks for the demand data."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def has_complete_realised_demand(
        self, start_utc: datetime, end_utc: datetime, house_config_id: str
    ) -> bool:
        """"""
        expected_count = int((end_utc - start_utc).total_seconds()) / (60 * 5)
        query = """
        SELECT COUNT(*) FROM realised_demand_5m
        WHERE house_config_id = ? AND timestamp_utc >= ? AND timestamp_utc < ?"""
        count = self.conn.execute(
            query, (house_config_id, start_utc.isoformat(), end_utc.isoformat())
        ).fetchone()[0]
        return count >= expected_count

    def has_complete_forecast_demand(
        self,
        start_utc: datetime,
        end_utc: datetime,
        house_config_id: str,
    ) -> bool:
        """"""
        # The last half-hour solve also needs its following 48 forecast targets.
        forecast_end = end_utc + timedelta(hours=23.5)
        expected_count = int((forecast_end - start_utc) / timedelta(minutes=30))
        query = """
        SELECT COUNT(*) FROM forecast_demand_30m
        WHERE house_config_id = ? AND target_time_utc >= ? AND target_time_utc < ?
            AND run_time_utc =
                substr(target_time_utc, 1, 10) || 'T00:00:00+00:00'"""
        count = self.conn.execute(
            query, (house_config_id, start_utc.isoformat(), forecast_end.isoformat())
        ).fetchone()[0]
        return count >= expected_count

    def save_realised_demand(self, records: list[RealisedDemandRow]) -> None:
        """"""
        query = """
        INSERT OR IGNORE INTO realised_demand_5m
        (timestamp_utc, house_config_id, demand_kw)
        VALUES (?, ?, ?)
        """
        rows = [
            (
                r.timestamp_utc.isoformat(),
                r.house_config_id,
                r.demand_kw,
            )
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def save_forecast_demand(self, records: list[ForecastDemandRow]) -> None:
        """"""
        query = """
        INSERT OR IGNORE INTO forecast_demand_30m
        (run_time_utc, target_time_utc, house_config_id, demand_kw)
        VALUES (?, ?, ?, ?)
        """
        rows = [
            (
                r.run_time_utc.isoformat(),
                r.target_time_utc.isoformat(),
                r.house_config_id,
                r.demand_kw,
            )
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()
