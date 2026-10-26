import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from database_v2.models.data_models import (
    ECMWFWeatherRow,
    OpenMeteoWeatherRow,
)


class WeatherDao:
    """Handles db writes and completeness checks for weather data."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    @staticmethod
    def _openmeteo_bounds(
        start_utc: datetime, end_utc: datetime
    ) -> tuple[datetime, datetime]:
        start = start_utc.replace(
            minute=start_utc.minute // 10 * 10, second=0, microsecond=0
        )
        end = end_utc.replace(minute=end_utc.minute // 10 * 10, second=0, microsecond=0)
        if end < end_utc:
            end += timedelta(minutes=10)
        return start, end

    def has_complete_openmeto_weather(
        self, start_utc: datetime, end_utc: datetime, location_name: str
    ) -> bool:
        """Check native 10-minute radiation including the final interval endpoint."""
        source_start, source_end = self._openmeteo_bounds(start_utc, end_utc)
        query = """
        SELECT timestamp_utc FROM raw_openmeteo
        WHERE location_name = ? AND timestamp_utc >= ? AND timestamp_utc <= ?"""
        rows = self.conn.execute(
            query, (location_name, source_start.isoformat(), source_end.isoformat())
        ).fetchall()
        available = {row[0] for row in rows}
        expected = {
            (source_start + timedelta(minutes=10 * offset)).isoformat()
            for offset in range(
                int((source_end - source_start) / timedelta(minutes=10)) + 1
            )
        }
        return expected.issubset(available)

    def has_complete_ecmwf_weather(
        self, start_utc: datetime, end_utc: datetime, location_name: str
    ) -> bool:
        """ """
        expected_count = int((end_utc - start_utc).total_seconds() / (3600 * 0.5))
        query = """
        SELECT COUNT(*) FROM raw_ecmwf
        WHERE location_name = ? AND timestamp_utc >= ? AND timestamp_utc < ?"""
        count = self.conn.execute(
            query, (location_name, start_utc.isoformat(), end_utc.isoformat())
        ).fetchone()[0]
        return count >= expected_count

    def has_complete_ecmwf_cycles(
        self, cycles: set[datetime], location_name: str
    ) -> bool:
        """Check the 54-hour half-hour grid for every needed forecast cycle."""
        return not self.get_missing_ecmwf_cycles(cycles, location_name)

    def get_missing_ecmwf_cycles(
        self, cycles: set[datetime], location_name: str
    ) -> set[datetime]:
        """Return cycles without a complete 54-hour half-hour grid."""
        missing = set()
        for cycle in cycles:
            rows = self.conn.execute(
                """SELECT timestamp_utc FROM raw_ecmwf
                WHERE location_name = ? AND available_at_utc = ?""",
                (location_name, cycle.isoformat()),
            ).fetchall()
            available = {row[0] for row in rows}
            expected = {
                (cycle + timedelta(minutes=30 * offset)).isoformat()
                for offset in range(109)
            }
            if not expected.issubset(available):
                missing.add(cycle)
        return missing

    def get_openmeteo_weather(
        self, start_utc: datetime, end_utc: datetime, location_name: str
    ) -> list[OpenMeteoWeatherRow]:
        source_start, source_end = self._openmeteo_bounds(start_utc, end_utc)
        query = """
            SELECT * FROM raw_openmeteo
            WHERE location_name = ? AND timestamp_utc >= ? AND timestamp_utc <= ?
            ORDER BY timestamp_utc ASC"""

        rows = self.conn.execute(
            query, (location_name, source_start.isoformat(), source_end.isoformat())
        ).fetchall()

        return [OpenMeteoWeatherRow(**dict(r)) for r in rows]

    def get_ecmwf_weather(
        self, start_utc: datetime, end_utc: datetime, location_name: str
    ) -> list[ECMWFWeatherRow]:
        query = """
            SELECT * FROM raw_ecmwf
            WHERE location_name = ? AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc ASC"""

        rows = self.conn.execute(
            query, (location_name, start_utc.isoformat(), end_utc.isoformat())
        ).fetchall()

        return [ECMWFWeatherRow(**dict(r)) for r in rows]

    def save_openmeteo_weather(self, records: list[OpenMeteoWeatherRow]) -> None:
        query = """
        INSERT OR REPLACE INTO raw_openmeteo
        (timestamp_utc, location_name, ghi, dhi, dni, air_temperature, wind_speed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                r.timestamp_utc.isoformat(),
                r.location_name,
                r.ghi,
                r.dhi,
                r.dni,
                r.air_temperature,
                r.wind_speed,
            )
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def save_emcwf_weather(self, records: list[ECMWFWeatherRow]) -> None:
        query = """
        INSERT OR REPLACE INTO raw_ecmwf
        (timestamp_utc, available_at_utc, location_name, ghi, air_temperature, \
        wind_speed)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                r.timestamp_utc.isoformat(),
                r.available_at_utc.isoformat(),
                r.location_name,
                r.ghi,
                r.air_temperature,
                r.wind_speed,
            )
            for r in records
        ]
        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(query, rows)
        self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()
