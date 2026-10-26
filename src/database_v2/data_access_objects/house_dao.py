import logging
import sqlite3
from pathlib import Path

from database_v2.models.request_models import HouseConfig

logger = logging.getLogger(__name__)


class HouseConfigDao:
    """Handles db retrieval or insertion of house configurations."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def get_all_house_configs(self) -> list[HouseConfig]:
        """Gets all registered house configs from the database."""

        query = """SELECT * FROM house_config ORDER BY house_config_id ASC;"""
        rows = self.conn.execute(query).fetchall()

        return [HouseConfig(**dict(r)) for r in rows]

    def get_house_config(self, house_config_id: str) -> HouseConfig | None:
        """Gets a single house config by its primary key."""

        query = """SELECT * FROM house_config WHERE house_config_id = ?;"""
        row = self.conn.execute(query, (house_config_id,)).fetchone()

        if row is None:
            return None

        return HouseConfig(**dict(row))

    def save_house_config(self, house_config: HouseConfig) -> None:
        """
        Saves a single house config. If a house config already exists,
        its attributes are safely updated without crashing.
        """

        query = """
            INSERT INTO house_config (
                house_config_id,
                pv_peak_capacity,
                panel_tilt,
                panel_azimuth,
                pv_inverter_ac_kw,
                pv_inverter_efficiency,
                has_aircon,
                has_poolpump,
                has_ev
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(house_config_id) DO UPDATE SET
                pv_peak_capacity=excluded.pv_peak_capacity,
                panel_tilt=excluded.panel_tilt,
                panel_azimuth=excluded.panel_azimuth,
                pv_inverter_ac_kw=excluded.pv_inverter_ac_kw,
                pv_inverter_efficiency=excluded.pv_inverter_efficiency,
                has_aircon=excluded.has_aircon,
                has_poolpump=excluded.has_poolpump,
                has_ev=excluded.has_ev;
        """

        self.conn.execute(
            query,
            (
                house_config.house_config_id,
                house_config.pv_peak_capacity,
                house_config.panel_tilt,
                house_config.panel_azimuth,
                house_config.pv_inverter_ac_kw,
                house_config.pv_inverter_efficiency,
                house_config.has_aircon,
                house_config.has_poolpump,
                house_config.has_ev,
            ),
        )

    def close(self) -> None:
        """Closes the underlying database connection."""
        self.conn.close()
