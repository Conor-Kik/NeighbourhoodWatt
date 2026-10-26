import sqlite3
from pathlib import Path

from database_v2.models.request_models import Location


class LocationDao:
    """Handles database retrieval and insertion of locations."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def get_all_locations(self) -> list[Location]:
        """Gets all registered locations from the database."""

        query = """SELECT * FROM location ORDER BY location_name ASC;"""
        rows = self.conn.execute(query).fetchall()

        return [Location(**dict(r)) for r in rows]

    def get_location(self, location_name: str) -> Location | None:
        """Gets a single location by its primary key."""

        query = """SELECT * FROM location WHERE location_name = ?;"""
        row = self.conn.execute(query, (location_name,)).fetchone()

        if row is None:
            return None

        return Location(**dict(row))

    def save_location(self, location: Location) -> None:
        """
        Saves a single location. If a location already exists,
        its coordinates and regions are safely updated without crashing.
        """

        query = """
            INSERT INTO location (
                location_name,
                latitude,
                longitude,
                timezone,
                nem_region
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(location_name) DO UPDATE SET
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                timezone=excluded.timezone,
                nem_region=excluded.nem_region;
        """

        self.conn.execute(
            query,
            (
                location.location_name,
                location.latitude,
                location.longitude,
                location.timezone,
                location.nem_region,
            ),
        )
