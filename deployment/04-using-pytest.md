# Guide to Testing with Pytest

This guide covers how to write, execute, and maintain unit and integration tests across the repository using pytest.
Tests can be run directly or through the optional pre-commit hooks. See [tests/README.md](../tests/README.md) for unit-only runs, full GUI simulations, and explicit coverage options.

## 4.1 Running Tests
Activate the project environment with `conda activate neighbourhoodwatt` before running tests.

**Run Full Test Suite**
```
pytest
```

**Run a Specific Test File**
```
pytest tests/.../<test_file_name>.py
```

**Run Tests by Keyword or Function Name**
Run only test functions matching a string pattern:
```
pytest -k "test_string_pattern"
```

**Common Flags**
 * `-v`: verbose output showing each test status
 * `-x`: stop execution immediately on first failure
 * `-s`: show print statements during test execution

## 4.2 Test Suite Layout & Conventions
The `tests/` directory should mirror the layout of the `src/` directory to keep unit test suites organized and maintainable.
Test files and functions must start with `test_` (e.g., `test_forecast_service.py` and `def test_dispatch_price_calculation(): `).

## 4.3 Writing Tests & Pytest Patterns
 * Use standard Python assert statements. Keep test setup clean by constructing synthetic test data explicitly within the test file.
 * Use `pytest.raises` to verify that invalid inputs or error states throw expected exceptions.
 * Define reusable fixtures using `@pytest.fixture` to share setup logic across multiple tests within a file.
 * For network operations (e.g., AEMO data downloads via NEMOSIS) or external solver calls, use unittest.mock to mock heavy dependencies.
