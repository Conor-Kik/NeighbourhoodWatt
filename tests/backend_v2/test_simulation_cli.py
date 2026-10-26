"""Local, network-free smoke test for the v2 simulation CLI."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call
from zoneinfo import ZoneInfo

import pytest
from team_21_the_grid_optimists import cli as cli_module
from typer.testing import CliRunner

from backend_v2.aemo.aemo_emissions_service import IncompleteEmissionsDataError
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.db_initilisation import reset_db
from database_v2.models.optimiser_models import Solver


def test_simulation_cli_rejects_baseline_forecasts() -> None:
    result = CliRunner().invoke(
        cli_module.cli, ["run-simulation", "--baseline-forecasts"]
    )
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_simulation_cli_reports_unavailable_dates_without_traceback(
    tmp_path, monkeypatch, caplog
) -> None:
    db_path = tmp_path / "unavailable-dates.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    pipeline = MagicMock()
    message = "Cannot use this date range: emissions data is incomplete on 2025-09-30."
    pipeline.ensure_data.side_effect = IncompleteEmissionsDataError(message)
    prepare = cli_module.ensure_data
    monkeypatch.setattr(
        cli_module,
        "ensure_data",
        lambda request, path: prepare(request, path, [pipeline]),
    )
    engine = MagicMock()
    monkeypatch.setattr(cli_module, "SimulationEngine", engine)

    result = CliRunner().invoke(
        cli_module.cli, ["run-simulation", "--start", "2025-09-10"]
    )

    assert result.exit_code == 1
    assert message in " ".join(result.output.split())
    assert "Traceback" not in result.output
    assert any(record.getMessage() == message for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)
    engine.assert_not_called()


def test_completed_simulation_cli_reads_summaries(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cached.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    dao = MagicMock()
    dao.run_exists.return_value = True
    monkeypatch.setattr(cli_module, "SimulationDao", lambda *args: dao)
    printer = MagicMock()
    monkeypatch.setattr(cli_module, "SimulationPrinter", lambda *args: printer)

    def unexpected(*args, **kwargs):
        pytest.fail("Completed simulations must not prepare data or run a solver")

    monkeypatch.setattr(cli_module, "SimulationEngine", unexpected)
    monkeypatch.setattr(cli_module, "ensure_data", unexpected)
    result = CliRunner().invoke(
        cli_module.cli, ["run-simulation", "--start", "2026-07-15"]
    )
    assert result.exit_code == 0, result.output
    printer.print_stored_summaries.assert_called_once()
    printer.print_reactive_summary.assert_called_once()
    printer.print_terminal_value_summary.assert_called_once()
    assert printer.method_calls[-1] == call.print_stored_summaries()
    dao.close.assert_called_once()


def test_default_simulation_start_uses_published_archive_month() -> None:
    now = datetime(2026, 9, 28, 13, 45, tzinfo=UTC)
    assert cli_module._archive_cutoff_utc(now) == datetime(2026, 7, 31, 14, tzinfo=UTC)
    assert cli_module._default_simulation_start(now, 2) == datetime(
        2026, 7, 30, tzinfo=UTC
    )
    long_start = cli_module._default_simulation_start(now, 40 * 24)
    assert long_start + timedelta(days=41) <= cli_module._archive_cutoff_utc(now)
    assert long_start < cli_module._default_simulation_start(now, 2)


@pytest.mark.parametrize(
    ("now", "timezone"),
    [
        (datetime(2026, 9, 28, 13, 45, tzinfo=UTC), "Australia/Brisbane"),
        (datetime(2027, 3, 28, 13, 45, tzinfo=UTC), "Australia/Sydney"),
    ],
)
def test_default_simulation_start_is_4_am_local_and_archive_safe(
    now: datetime, timezone: str
) -> None:
    start = cli_module._default_simulation_start(now, 26, timezone, local_hour=4)
    local_start = start.astimezone(ZoneInfo(timezone))
    assert (local_start.hour, local_start.minute) == (4, 0)
    assert start + timedelta(hours=26 + 24) <= cli_module._archive_cutoff_utc(now)


def test_simulation_cli_defaults_to_4_am_in_selected_location(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "default-start.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        cli_module.DemandPipeline, "ensure_data", lambda self, request, path: None
    )
    captured_requests = []

    class FakeEngine:
        def __init__(self, request, db_path, solver):
            assert solver is Solver.GUROBI
            captured_requests.append(request)

        def run(self, *, on_step):
            return []

    monkeypatch.setattr(cli_module, "SimulationEngine", FakeEngine)
    result = CliRunner().invoke(
        cli_module.cli,
        ["run-simulation", "--location", "Sydney", "--no-prepare-data"],
    )
    assert result.exit_code == 0, result.output
    start = captured_requests[0].start_time_utc.astimezone(ZoneInfo("Australia/Sydney"))
    assert (start.hour, start.minute) == (4, 0)


@pytest.mark.parametrize(
    ("start_arg", "expected_start_utc"),
    [
        ("2026-03-10", datetime(2026, 3, 9, 17, 30, tzinfo=UTC)),
        ("2026-03-10T00:00:00", datetime(2026, 3, 10, tzinfo=UTC)),
        ("2026-03-10T04:00:00+10:30", datetime(2026, 3, 9, 17, 30, tzinfo=UTC)),
    ],
)
def test_simulation_cli_date_only_uses_local_4_am_but_datetime_keeps_utc(
    tmp_path, monkeypatch, start_arg, expected_start_utc
) -> None:
    db_path = tmp_path / "start-input.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        cli_module.DemandPipeline, "ensure_data", lambda self, request, path: None
    )
    captured_requests = []

    class FakeEngine:
        def __init__(self, request, db_path, solver):
            captured_requests.append(request)

        def run(self, *, on_step):
            return []

    monkeypatch.setattr(cli_module, "SimulationEngine", FakeEngine)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "run-simulation",
            "--location",
            "Adelaide",
            "--start",
            start_arg,
            "--no-prepare-data",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured_requests[0].start_time_utc == expected_start_utc


@pytest.mark.parametrize(
    ("duration_args", "expected_hours"),
    [
        ([], 1),
        (["--days", "1"], 24),
        (["--days", "1", "--hours", "2"], 26),
        (["--hours", "2"], 2),
    ],
)
def test_simulation_cli_duration(
    tmp_path, monkeypatch, duration_args, expected_hours
) -> None:
    db_path = tmp_path / "duration.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        cli_module.DemandPipeline, "ensure_data", lambda self, request, path: None
    )
    captured_requests = []

    class FakeEngine:
        def __init__(self, request, db_path, solver):
            captured_requests.append(request)

        def run(self, *, on_step):
            return []

    monkeypatch.setattr(cli_module, "SimulationEngine", FakeEngine)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "run-simulation",
            "--start",
            "2026-07-15",
            "--no-prepare-data",
            *duration_args,
        ],
    )
    assert result.exit_code == 0, result.output
    request = captured_requests[0]
    assert request.end_time_utc - request.start_time_utc == timedelta(
        hours=expected_hours
    )


def test_simulation_cli_prints_steps_before_engine_returns(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "streaming.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        cli_module.DemandPipeline, "ensure_data", lambda self, request, path: None
    )
    events = []
    completed_steps = [object(), object()]

    class FakeEngine:
        perfect_result = ("perfect", ["decision"])

        def __init__(self, request, db_path, solver):
            pass

        def run(self, *, on_step):
            events.append("engine started")
            on_step(completed_steps[0])
            events.append("engine mid-run")
            on_step(completed_steps[1])
            events.append("engine finished")
            return completed_steps

    monkeypatch.setattr(cli_module, "SimulationEngine", FakeEngine)

    class FakePrinter:
        def __init__(self, request, db_path):
            pass

        def print_step(self, step):
            events.append("printed step")

        def print_stored_summaries(self):
            events.append("printed final table")

        def print_summary(self, steps):
            events.append("printed summary")

        def print_perfect_foresight_summary(self, steps, data, decisions):
            assert data == "perfect"
            assert decisions == ["decision"]
            events.append("printed perfect foresight")

        def print_no_battery_summary(self):
            events.append("printed no battery")

        def print_reactive_summary(self):
            events.append("printed reactive")

    monkeypatch.setattr(cli_module, "SimulationPrinter", FakePrinter)
    result = CliRunner().invoke(
        cli_module.cli,
        ["run-simulation", "--start", "2026-07-15", "--no-prepare-data"],
    )
    assert result.exit_code == 0, result.output
    assert events == [
        "engine started",
        "printed step",
        "engine mid-run",
        "printed step",
        "engine finished",
        "printed summary",
        "printed perfect foresight",
        "printed no battery",
        "printed reactive",
        "printed final table",
    ]


def test_simulation_cli_randomly_assigns_houses_to_distinct_configs(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "random-configs.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        cli_module.DemandPipeline, "ensure_data", lambda self, request, path: None
    )
    requests = []

    class FakeEngine:
        def __init__(self, request, db_path, solver):
            requests.append(request)

        def run(self, *, on_step):
            return []

    monkeypatch.setattr(cli_module, "SimulationEngine", FakeEngine)
    args = [
        "run-simulation",
        "--start",
        "2026-07-15",
        "--houses",
        "50",
        "--random-configs",
        "10",
        "--assignment-seed",
        "42",
        "--no-prepare-data",
    ]
    for _ in range(2):
        result = CliRunner().invoke(cli_module.cli, args)
        assert result.exit_code == 0, result.output
        assert "Assigned houses:" in result.output

    assert len(requests[0].houses) == 10
    assert sum(requests[0].houses.values()) == 50
    assert all(quantity > 0 for quantity in requests[0].houses.values())
    assert requests[0].houses == requests[1].houses

    config_dao = HouseConfigDao(db_path)
    configs = config_dao.get_all_house_configs()
    config_dao.close()
    assigned = requests[0].houses
    assert sum(assigned[c.house_config_id] for c in configs if c.has_poolpump) <= 15
    assert (
        sum(assigned[c.house_config_id] for c in configs if c.pv_peak_capacity >= 20)
        <= 3
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--random-configs", "0"],
        ["--random-configs", "4", "--houses", "3"],
        ["--random-configs", "11"],
        ["--random-configs", "2", "--house-config", "house-1--6.6KW-5KW-INV"],
    ],
)
def test_simulation_cli_rejects_invalid_random_config_selection(
    tmp_path, monkeypatch, args
) -> None:
    db_path = tmp_path / "invalid-random-configs.db"
    reset_db(db_path)
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    result = CliRunner().invoke(cli_module.cli, ["run-simulation", *args])
    assert result.exit_code != 0


@pytest.mark.parametrize(
    "duration_args",
    [["--days", "-1"], ["--hours", "0"], ["--hours", "0.25"]],
)
def test_simulation_cli_rejects_invalid_duration(
    tmp_path, monkeypatch, duration_args
) -> None:
    db_path = tmp_path / "duration.db"
    db_path.touch()
    monkeypatch.setattr(cli_module, "DB_PATH", db_path)
    result = CliRunner().invoke(cli_module.cli, ["run-simulation", *duration_args])
    assert result.exit_code != 0
