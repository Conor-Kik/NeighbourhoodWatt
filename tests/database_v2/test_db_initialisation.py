import sqlite3

import pytest

from database_v2.db_initilisation import reset_db


@pytest.fixture
def mock_db_env(tmp_path, monkeypatch):
    """
    Sets up a temporary directory with a dummy schema and seed files,
    then patches the constants in your module so it uses these temp paths.
    """
    db_path = tmp_path / "test.db"
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()

    (schema_dir / "01_schema.sql").write_text(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT);"
    )

    (seed_dir / "01_seed.sql").write_text(
        "INSERT INTO test_table (name) VALUES ('Alice');"
    )
    (seed_dir / "02_seed.sql").write_text(
        "INSERT INTO test_table (name) VALUES ('Bob');"
    )

    monkeypatch.setattr("database_v2.db_initilisation.SCHEMA_DIR", schema_dir)
    monkeypatch.setattr("database_v2.db_initilisation.SEED_DIR", seed_dir)
    monkeypatch.setattr("database_v2.db_initilisation.DB_PATH", db_path)

    return db_path, schema_dir, seed_dir


def test_reset_db_success(mock_db_env):
    """Tests that a fresh database is created, schema is run, and seeds are inserted."""
    db_path, _, _ = mock_db_env

    reset_db(db_path)

    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM test_table ORDER BY name;")
        results = cursor.fetchall()
        assert results == [("Alice",), ("Bob",)]


def test_reset_db_deletes_existing_db(mock_db_env):
    """Tests that an existing database file is deleted before running."""
    db_path, _, _ = mock_db_env

    db_path.write_text("dummy garbage data")

    reset_db(db_path)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM test_table;")
        assert cursor.fetchone()[0] == 2


def test_reset_db_seed_failure_rolls_back_and_raises(mock_db_env, caplog):
    """Tests that invalid SQL in a seed file logs an error and raises."""
    db_path, _, seed_dir = mock_db_env

    bad_seed = seed_dir / "03_bad_seed.sql"
    bad_seed.write_text("INSERT INTO non_existent_table VALUES ('Error');")

    with pytest.raises(sqlite3.Error):
        reset_db(db_path)

    assert "db reset failed on" in caplog.text
    assert "03_bad_seed.sql" in caplog.text


@pytest.mark.parametrize("directory", ["SCHEMA_DIR", "SEED_DIR"])
def test_missing_sql_assets_preserve_existing_database(
    tmp_path, monkeypatch, directory
):
    database = tmp_path / "existing.db"
    database.write_bytes(b"existing database must survive missing assets")
    monkeypatch.setattr(
        f"database_v2.db_initilisation.{directory}", tmp_path / "missing"
    )
    with pytest.raises(FileNotFoundError, match="files are missing"):
        reset_db(database)
    assert database.read_bytes() == b"existing database must survive missing assets"
