import logging
import sqlite3
from pathlib import Path
from typing import Any

from database_v2.models.request_models import SimulationRequest

logger = logging.getLogger(__name__)


class RunDao:
    """Reads finished simulation runs (requests) and their outputs, for viewing.
    Unlike SimulationDao and ResultsDao, it is not tied to one request."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def get_all_simulation_requests(self) -> list[SimulationRequest]:
        """Gets every saved simulation request with its houses, newest start
        time first."""

        requests = self.conn.execute(
            """SELECT * FROM simulation_request
               ORDER BY start_time_utc DESC, request_id ASC;"""
        ).fetchall()
        houses = self.conn.execute(
            "SELECT request_id, house_config_id, quantity FROM simulation_house;"
        ).fetchall()

        houses_by_request: dict[str, dict[str, int]] = {}
        for row in houses:
            houses_by_request.setdefault(row["request_id"], {})[
                row["house_config_id"]
            ] = row["quantity"]

        return [
            SimulationRequest(
                **dict(r), houses=houses_by_request.get(r["request_id"], {})
            )
            for r in requests
        ]

    def get_optimiser_outputs(self, request_id: str) -> list[dict[str, Any]]:
        """Gets a request's 30 minute optimiser outputs, oldest first."""

        query = """SELECT * FROM optimiser_output_30m
                   WHERE request_id = ?
                   ORDER BY timestamp_utc ASC;"""

        return [dict(r) for r in self.conn.execute(query, (request_id,))]

    def get_simulation_summaries(self, request_id: str) -> list[dict[str, Any]]:
        """Gets a request's summary totals, one per scenario."""

        query = """SELECT * FROM simulation_summary
                   WHERE request_id = ?
                   ORDER BY scenario_name ASC;"""

        return [dict(r) for r in self.conn.execute(query, (request_id,))]

    def close(self) -> None:
        self.conn.close()
