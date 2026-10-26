"""Tests for the OptimiserGateway's link to the replay clock."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from frontend_v2.engine.optimiser_gateway import OptimiserGateway, SimulationWorker
from frontend_v2.sources.replay_clock import DEFAULT_MAX_T, ReplayClock
from tests.factories import SimulationRequestFactory


def make_clock(max_t: int = DEFAULT_MAX_T) -> ReplayClock:
    """A replay clock for a day starting at midnight."""
    return ReplayClock(start_time=datetime(2026, 10, 11), max_t=max_t)


def make_gateway(max_t: int = DEFAULT_MAX_T) -> OptimiserGateway:
    """An OptimiserGateway holding a replay clock, with no simulation started."""
    return OptimiserGateway(":memory:", make_clock(max_t))


def test_gateway_holds_the_clock_it_is_given() -> None:
    clock = make_clock()
    gateway = OptimiserGateway(":memory:", clock)

    assert gateway.replay_clock is clock


def test_completed_step_moves_the_optimiser_head() -> None:
    """A step completed half an hour into the replay is the 6th clock step."""
    gateway = make_gateway()

    gateway._record_optimiser_step(datetime(2026, 10, 11, 0, 30))

    assert gateway.replay_clock.t_optimiser == 6


def test_completed_step_on_the_final_boundary_is_recorded() -> None:
    gateway = make_gateway(max_t=10)

    gateway._record_optimiser_step(datetime(2026, 10, 11, 0, 50))

    assert gateway.replay_clock.t_optimiser == 10


def test_completed_step_outside_the_replay_raises() -> None:
    gateway = make_gateway(max_t=10)

    with pytest.raises(ValueError, match="falls outside the replay"):
        gateway._record_optimiser_step(datetime(2026, 10, 12))


def test_worker_reports_a_step_as_a_datetime() -> None:
    """The worker turns the engine's ISO timestamp into a datetime once."""
    worker = SimulationWorker(SimulationRequestFactory.build(), ":memory:")
    steps: list[datetime] = []
    worker.step_completed.connect(steps.append)

    worker._on_step("2026-10-11T00:30:00")

    assert steps == [datetime(2026, 10, 11, 0, 30)]


@pytest.mark.parametrize("days", [1, 3])
def test_start_simulation_sets_the_clock_before_worker_progress(monkeypatch, days):
    clock = ReplayClock()
    clock.seek(10)
    clock.set_t_optimiser(12)
    gateway = OptimiserGateway(":memory:", clock)
    start = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=11)))
    request = SimulationRequestFactory.build(
        start_time_utc=start, end_time_utc=start + timedelta(days=days)
    )
    times = []
    progress = []
    clock.time_changed.connect(times.append)
    clock.optimiser_changed.connect(progress.append)

    def start_worker(worker):
        assert clock.start_time == start.astimezone(UTC)
        assert clock.end_time == request.end_time_utc.astimezone(UTC)
        assert clock.t == clock.t_optimiser == 0
        worker._on_step((start + timedelta(minutes=30)).isoformat())
        assert clock.t_optimiser == 6
        worker._on_step(request.end_time_utc.isoformat())

    monkeypatch.setattr(SimulationWorker, "start", start_worker)
    gateway.start_simulation(request.model_dump())

    assert clock.max_t == days * DEFAULT_MAX_T
    assert clock.t_optimiser == clock.max_t
    assert times == [start.astimezone(UTC)]
    assert progress == [0, 6, clock.max_t]
