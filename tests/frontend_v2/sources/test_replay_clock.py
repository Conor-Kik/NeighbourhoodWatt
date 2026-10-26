from datetime import UTC, datetime

import pytest

from frontend_v2.sources.replay_clock import (
    DEFAULT_MAX_T,
    STEP_DURATION,
    UNSET_START_TIME,
    UNSET_T_OPTIMISER,
    ReplayClock,
)


def make_clock(max_t: int = DEFAULT_MAX_T) -> ReplayClock:
    return ReplayClock(start_time=datetime(2026, 10, 11), max_t=max_t)


def test_duration_reports_the_replay_window_in_minutes():
    clock = make_clock()

    assert clock.duration_minutes == DEFAULT_MAX_T * STEP_DURATION


def test_duration_follows_a_non_default_window():
    clock = make_clock(max_t=10)

    assert clock.duration_minutes == 10 * STEP_DURATION


def test_t_optimiser_starts_unreported():
    clock = make_clock()

    assert clock.t_optimiser is UNSET_T_OPTIMISER


def test_set_t_optimiser_records_the_step_and_emits_it():
    clock = make_clock()
    completed = []
    clock.optimiser_changed.connect(completed.append)

    clock.set_t_optimiser(7)

    assert clock.t_optimiser == 7
    assert completed == [7]


def test_set_t_optimiser_accepts_the_final_step():
    clock = make_clock(max_t=10)

    clock.set_t_optimiser(10)

    assert clock.t_optimiser == 10


def test_set_t_optimiser_raises_outside_the_replay():
    clock = make_clock(max_t=10)

    with pytest.raises(ValueError, match="optimiser step outside the replay"):
        clock.set_t_optimiser(11)


def test_moving_the_replay_head_leaves_the_optimiser_head_alone():
    clock = make_clock(max_t=10)

    clock.seek(3)

    assert clock.t_optimiser is UNSET_T_OPTIMISER
    assert clock.t == 3


def test_seek_raises_outside_the_replay():
    clock = make_clock(max_t=10)

    with pytest.raises(ValueError, match="Cannot seek less than 0, or beyond max t"):
        clock.seek(10)


def test_set_t_optimiser_rejects_an_unreported_step():
    clock = make_clock(max_t=10)

    with pytest.raises(ValueError, match="optimiser step outside the replay"):
        clock.set_t_optimiser(UNSET_T_OPTIMISER)


def test_reset_sends_the_clock_back_to_no_day_at_all():
    clock = make_clock(max_t=10)
    clock.seek(3)
    clock.set_t_optimiser(4)

    clock.reset()

    assert clock.start_time == UNSET_START_TIME
    assert clock.t == 0
    assert clock.t_optimiser is UNSET_T_OPTIMISER


def test_reset_stops_a_clock_left_running():
    clock = make_clock(max_t=10)
    clock.play()

    clock.reset()

    assert not clock.is_running


def test_reset_emits_both_faces_the_clock_publishes():
    clock = make_clock(max_t=10)
    times = []
    heads = []
    clock.time_changed.connect(times.append)
    clock.optimiser_changed.connect(heads.append)

    clock.reset()

    assert times == [UNSET_START_TIME]
    assert heads == [UNSET_T_OPTIMISER]


def test_set_run_takes_the_window_from_the_run_itself():
    clock = make_clock(max_t=10)

    clock.set_run("2026-10-11T00:00:00+00:00", "2026-10-13T00:00:00+00:00")

    assert clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)
    assert clock.get_time() == datetime(2026, 10, 11, tzinfo=UTC)
    assert clock.duration_minutes == 2 * 24 * 60
    assert clock.t_optimiser is UNSET_T_OPTIMISER


def test_set_run_covers_a_run_that_is_not_whole_steps_long():
    clock = make_clock(max_t=10)
    with pytest.raises(ValueError, match="whole steps"):
        clock.set_run("2026-10-11T00:00:00+00:00", "2026-10-11T00:07:00+00:00")


def test_set_run_rejects_a_run_that_ends_before_it_starts():
    clock = make_clock(max_t=10)

    with pytest.raises(ValueError, match="must end after it starts"):
        clock.set_run("2026-10-11T01:00:00+00:00", "2026-10-11T00:00:00+00:00")


def test_set_run_returns_a_running_clock_to_the_top_of_the_run():
    clock = make_clock(max_t=10)
    clock.seek(3)
    clock.play()

    clock.set_run("2026-10-11T00:00:00+00:00", "2026-10-11T02:00:00+00:00")

    assert not clock.is_running
    assert clock.get_time() == datetime(2026, 10, 11, tzinfo=UTC)


def test_reset_puts_the_default_day_back_on_a_clock_given_a_longer_run():
    clock = make_clock()
    clock.set_run("2026-10-11T00:00:00+00:00", "2026-10-13T00:00:00+00:00")

    clock.reset()

    assert clock.max_t == DEFAULT_MAX_T


@pytest.mark.parametrize("speed,interval", [(1.0, 500), (1.5, 333), (2.0, 250)])
def test_speed_changes_pace_without_changing_position_or_paused_state(speed, interval):
    clock = make_clock()
    clock.seek(12)
    times = []
    clock.time_changed.connect(times.append)
    clock.set_playback_speed(speed)
    assert clock.playback_speed == speed
    assert clock._timer.interval() == interval
    assert not clock.is_running
    assert clock.t == 12
    assert times == []


@pytest.mark.parametrize("speed", [0, -1, 0.5, 3, 4, float("nan"), float("inf")])
def test_speed_rejects_unsupported_values_without_changing_clock(speed):
    clock = make_clock()
    with pytest.raises(ValueError, match="Unsupported playback speed"):
        clock.set_playback_speed(speed)
    assert clock.playback_speed == 1
    assert clock._timer.interval() == 500


def test_fast_playback_keeps_every_step_and_stops_normally():
    clock = make_clock(max_t=4)
    clock.set_playback_speed(2)
    steps = []
    clock.time_changed.connect(lambda _: steps.append(clock.t))
    clock.play()
    for _ in range(4):
        clock._advance_step()
    assert steps == [1, 2, 3]
    assert not clock.is_running
    clock.set_run("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00")
    assert clock.playback_speed == 2
    assert not clock.is_running


def test_speed_change_while_running_preserves_playback():
    clock = make_clock()
    clock.seek(5)
    clock.play()
    clock.set_playback_speed(2)
    assert clock.is_running
    assert clock.t == 5
    clock.pause()
