import sqlite3
from pathlib import Path

from database_v2.models.request_models import BatteryConfig


class BatteryDao:
    """Handles database retrieval and insertion of battery configs."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def get_all_battery_configs(self) -> list[BatteryConfig]:
        """Gets all registered battery configs from the database."""

        query = """SELECT * FROM battery_config ORDER BY battery_config_id ASC;"""
        rows = self.conn.execute(query).fetchall()

        return [BatteryConfig(**dict(r)) for r in rows]

    def get_battery_config(self, battery_config_id: str) -> BatteryConfig | None:
        """Gets a single battery config by its primary key."""

        query = """SELECT * FROM battery_config WHERE battery_config_id = ?;"""
        row = self.conn.execute(query, (battery_config_id,)).fetchone()

        if row is None:
            return None

        return BatteryConfig(**dict(row))

    def save_battery_config(self, battery_config: BatteryConfig) -> None:
        """
        Saves a single battery config. If a battery config already exists,
        its attributes are safely updated without crashing.
        """

        query = """
            INSERT INTO battery_config (
                battery_config_id,
                capacity_kwh,
                max_power_kw,
                efficiency,
                min_soc_frac,
                max_soc_frac,
                replacement_cost_aud,
                lifetime_cycles,
                max_cycles_24h
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(battery_config_id) DO UPDATE SET
                capacity_kwh=excluded.capacity_kwh,
                max_power_kw=excluded.max_power_kw,
                efficiency=excluded.efficiency,
                min_soc_frac=excluded.min_soc_frac,
                max_soc_frac=excluded.max_soc_frac,
                replacement_cost_aud=excluded.replacement_cost_aud,
                lifetime_cycles=excluded.lifetime_cycles,
                max_cycles_24h=excluded.max_cycles_24h;
        """

        self.conn.execute(
            query,
            (
                battery_config.battery_config_id,
                battery_config.capacity_kwh,
                battery_config.max_power_kw,
                battery_config.efficiency,
                battery_config.min_soc_frac,
                battery_config.max_soc_frac,
                battery_config.replacement_cost_aud,
                battery_config.lifetime_cycles,
                battery_config.max_cycles_24h,
            ),
        )
