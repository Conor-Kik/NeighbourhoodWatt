from abc import ABC, abstractmethod
from pathlib import Path

from database_v2.models.request_models import SimulationRequest


class BaseDataPipeline(ABC):
    """Abstract base class for all data ingestion and generation pipelines."""

    @abstractmethod
    def ensure_data(self, run_input: SimulationRequest, db_path: Path) -> None:
        """Checks data for the window. Generates/fetches and saves if missing."""
        pass
