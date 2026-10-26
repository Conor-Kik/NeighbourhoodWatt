# Automated tests

Activate the project environment and run from the repository root:

```sh
conda activate neighbourhoodwatt
pytest
```

The tests cover the backend, database, frontend, and executable build
configuration. `tests/integration/` exercises the complete GUI and can fetch
market and weather data, use the local application database, and take longer.
Run these integration tests in a disposable checkout with an initialised
database (`gridsync reset-db`).

For the unit and component suite, without the full GUI simulation runs:

```sh
pytest --ignore=tests/integration
```

`pytest.ini` selects PyQt6. The coverage options in `pyproject.toml` can be
enabled explicitly when needed:

```sh
pytest --ignore=tests/integration --cov=src/backend_v2 --cov=src/database_v2 --cov-report=term-missing --cov-fail-under=80
```

See [the testing guide](../deployment/04-using-pytest.md) for test conventions.
