import logging
import sqlite3
from pathlib import Path

from database_v2.db_constants import DB_PATH, SCHEMA_DIR, SEED_DIR

logger = logging.getLogger(__name__)


def reset_db(db_path: Path = DB_PATH, seed_db: bool = True) -> None:
    """
    Hard reset of the database.
    Initialise the database schema.
    Populate with seed data.
    """
    schema_files = sorted(SCHEMA_DIR.glob("*.sql"))
    seed_files = sorted(SEED_DIR.glob("*.sql")) if seed_db else []
    if not schema_files:
        raise FileNotFoundError(f"Database schema files are missing: {SCHEMA_DIR}")
    if seed_db and not seed_files:
        raise FileNotFoundError(f"Database seed files are missing: {SEED_DIR}")

    if db_path.exists():
        logger.info("Deleting old database: %s", db_path)
    db_path.unlink(missing_ok=True)

    conn = sqlite3.connect(db_path)

    try:
        for schema_file in schema_files:
            try:
                conn.executescript(schema_file.read_text())
            except sqlite3.Error as e:
                logger.error("db reset failed on %s: %s", schema_file, e)
                conn.rollback()
                raise

        if seed_db:
            for seed_file in seed_files:
                try:
                    conn.executescript(seed_file.read_text())
                except sqlite3.Error as e:
                    logger.error("db reset failed on %s: %s", seed_file, e)
                    conn.rollback()
                    raise

        conn.commit()
        if seed_db:
            logger.info("Database initialised at %s with schema and seed data", db_path)
        else:
            logger.info("Database initialised at %s with schema", db_path)

    except sqlite3.Error:
        raise

    finally:
        conn.close()
