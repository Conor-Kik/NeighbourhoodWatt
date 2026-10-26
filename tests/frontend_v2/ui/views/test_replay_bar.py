"""Tests for the replay bar panel.

The bar reads a TabularPayload that `select_replay_state` builds from three UI
keys the DataManager publishes; it writes back by driving the ReplayClock. These
tests build that same payload by hand, so they exercise the payload contract
without a DataManager, a database, or the grid.

Qt aborts the process when a widget is built before a QApplication exists, and
this host has no display, so the module pins the offscreen platform plugin and
shares one application across every test. PyQt6 loads its Qt libraries when it
is imported, so the platform plugin has to be chosen before that import happens.
"""

import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors.tabular import select_replay_state
from frontend_v2.engine.data_manager import (
    UI_DURATION_KEY,
    UI_OPTIMISER_KEY,
    UI_TIME_KEY,
)
from frontend_v2.sources.replay_clock import (
    STEP_DURATION,
    UNSET_START_TIME,
    UNSET_T_OPTIMISER,
    ReplayClock,
)
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.views.replay_bar import (
    OPTIMISER_PREFIX,
    PAYLOAD_HEADERS,
    TIME_FORMAT,
    ReplayBarView,
)
from frontend_v2.utils.payload import TabularPayload

START_TIME = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)
MAX_T = 24 * 60 // STEP_DURATION

# Qt needs one application for the whole process; a widget built before it
# exists aborts.
APP = QApplication.instance() or QApplication([])


def make_clock(start_time: datetime | None = START_TIME, max_t: int = MAX_T):
    return ReplayClock(start_time=start_time, max_t=max_t)


def make_payload(
    time, duration: int, optimiser_t: int | None = UNSET_T_OPTIMISER
) -> TabularPayload:
    """Build the payload select_replay_state publishes, without a DataManager.

    optimiser_t defaults to the clock's "no step reported yet" value, so a test
    that does not care about the optimiser head gets an unstepped one."""
    return TabularPayload(
        headers=list(PAYLOAD_HEADERS),
        rows=[(time, duration, optimiser_t)],
    )


def show(view: ReplayBarView, payload: TabularPayload) -> None:
    """Put a payload through the same entry point PanelController uses."""
    view.show_payload(payload)


def test_replay_bar_raises_without_a_clock():
    with pytest.raises(ValueError, match="ReplayBarView needs a ReplayClock"):
        ReplayBarView()


def test_payload_headers_are_the_three_the_bar_reads_back():
    assert PAYLOAD_HEADERS == ("Replay time", "Replay length", "Optimiser step")


def test_bar_shows_the_clock_time_from_the_payload():
    view = ReplayBarView(make_clock())
    show(view, make_payload(START_TIME, 1440))

    assert view.text().splitlines()[0] == START_TIME.strftime(TIME_FORMAT)


def test_bar_shows_the_replay_length_while_the_optimiser_has_not_stepped():
    view = ReplayBarView(make_clock())
    show(view, make_payload(START_TIME, 1440, UNSET_T_OPTIMISER))

    assert view.text().splitlines() == [
        START_TIME.strftime(TIME_FORMAT),
        " Total time: 1 day",
    ]


def test_bar_shows_the_optimiser_head_once_it_has_stepped():
    view = ReplayBarView(make_clock())
    show(view, make_payload(START_TIME, 1440, 7))

    assert view.text() == (f"{START_TIME.strftime(TIME_FORMAT)}\n{OPTIMISER_PREFIX}7")


def test_bar_shows_step_zero_as_a_step_the_optimiser_has_done():
    view = ReplayBarView(make_clock())
    show(view, make_payload(START_TIME, 1440, 0))

    assert view.text() == (f"{START_TIME.strftime(TIME_FORMAT)}\n{OPTIMISER_PREFIX}0")
    assert view._head_label.text().startswith(OPTIMISER_PREFIX)


def test_bar_shows_nothing_while_the_clock_has_no_day_to_replay():
    view = ReplayBarView(make_clock(start_time=None))
    show(view, make_payload(None, 1440))

    assert view.text() == view.empty_text
    assert view._body.currentWidget() is view._empty


def test_bar_raises_on_a_payload_with_no_row():
    view = ReplayBarView(make_clock())

    with pytest.raises(ValueError, match="no row to read"):
        show(view, TabularPayload(headers=list(PAYLOAD_HEADERS), rows=[]))


def test_bar_seeks_the_clock_it_was_given():
    clock = make_clock()
    view = ReplayBarView(clock)

    view.seek(12)

    assert clock.t == 12


def test_seek_redraws_the_time_the_clock_moved_to():
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(START_TIME, 1440))

    view.seek(12)

    assert view.text().splitlines()[0] == clock.get_time().strftime(TIME_FORMAT)
    assert clock.get_time() == START_TIME + timedelta(minutes=12 * STEP_DURATION)


def test_play_and_pause_move_the_clock_it_was_given():
    clock = make_clock()
    view = ReplayBarView(clock)

    view.pause()
    assert not clock.is_running

    view.play()
    assert clock.is_running


def test_scrubber_reaches_the_last_step_of_the_replay():
    clock = make_clock(max_t=10)
    view = ReplayBarView(clock)

    view.seek(9)
    assert clock.t == 9

    with pytest.raises(ValueError, match="Cannot seek less than 0, or beyond max t"):
        view.seek(10)


def test_bar_clears_the_optimiser_line_when_the_head_is_not_set():
    view = ReplayBarView(make_clock())
    show(view, make_payload(START_TIME, 1440, 5))
    assert OPTIMISER_PREFIX in view.text()

    show(view, make_payload(START_TIME, 1440, UNSET_T_OPTIMISER))

    assert OPTIMISER_PREFIX not in view.text()
    assert view.text().splitlines() == [
        START_TIME.strftime(TIME_FORMAT),
        " Total time: 1 day",
    ]


def test_bar_follows_the_clock_when_the_clock_is_reset():
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(START_TIME, 1440, 5))

    clock.reset()
    view._read_clock()

    assert view.text() == view.empty_text


def test_panel_draws_the_bar_when_a_run_is_picked_before_the_optimiser_steps():
    """The panel draws what the payload holds, not what its own clock holds: a
    picked run whose optimiser has not reported a step publishes None, which must
    fill the bar instead of reading as missing data and blanking it."""
    view = ReplayBarView(make_clock(start_time=None))
    panel = PanelController("replay_bar", view, read=ReadBinding(select_replay_state))

    panel.refresh(
        {
            UI_TIME_KEY: START_TIME,
            UI_DURATION_KEY: 1440,
            UI_OPTIMISER_KEY: UNSET_T_OPTIMISER,
        }
    )

    assert view.text() == (f"{START_TIME.strftime(TIME_FORMAT)}\n Total time: 1 day")


def test_panel_shows_empty_until_a_run_is_given_to_the_clock():
    view = ReplayBarView(make_clock(start_time=None))
    panel = PanelController("replay_bar", view, read=ReadBinding(select_replay_state))

    panel.refresh(
        {
            UI_TIME_KEY: UNSET_START_TIME,
            UI_DURATION_KEY: 1440,
            UI_OPTIMISER_KEY: UNSET_T_OPTIMISER,
        }
    )

    assert view.text() == view.empty_text
    assert view._body.currentWidget() is view._empty


def test_a_none_ui_value_is_not_an_empty_buffer():
    """PanelController blanks a panel on an empty buffer; a UI key holds no rows,
    so even None must not blank it, or the unstepped optimiser hides the bar."""
    assert PanelController._is_empty(None) is False
    assert PanelController._is_empty(1440) is False
    assert PanelController._is_empty(START_TIME) is False
    assert PanelController._is_empty([]) is True
    assert PanelController._is_empty([1]) is False


def test_scrubber_follows_playback_without_emitting_a_user_seek():
    clock = make_clock()
    view = ReplayBarView(clock)
    seeks = []
    view._scrubber.valueChanged.connect(seeks.append)
    clock.seek(12)
    show(view, make_payload(clock.get_time(), clock.duration_minutes))
    assert view._scrubber.value() == 12
    assert seeks == []


def test_shorter_run_redraw_does_not_change_clock_position():
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(clock.get_time(), clock.duration_minutes))
    view._scrubber.setValue(100)
    clock.set_run("2026-10-11T07:00:00+00:00", "2026-10-11T07:50:00+00:00")
    show(view, make_payload(clock.get_time(), clock.duration_minutes))
    assert clock.t == view._scrubber.value() == 0


def test_video_controls_fit_narrow_width_and_preserve_exact_seek():
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(START_TIME, clock.duration_minutes))
    view.resize(480, 180)
    view.show()
    APP.processEvents()
    assert view._scrubber.width() > 80
    view._scrubber.setValue(37)
    assert clock.t == 37
    assert view._scrubber.accessibleName() == "Replay timeline"
    assert not view._scrubber.hasTracking()
    assert view._position_label.text() == "03:05 / 24:00"
    view.close()


def test_play_button_toggles_and_follows_automatic_stop():
    clock = make_clock(max_t=3)
    view = ReplayBarView(clock)
    view._play_button.click()
    assert clock.is_running
    assert view._play_button.accessibleName() == "Pause replay"
    clock.seek(2)
    clock._advance_step()
    assert not clock.is_running
    assert view._play_button.accessibleName() == "Play replay"
    view._play_button.click()
    assert clock.t == 0
    assert clock.is_running
    view._play_button.click()
    assert not clock.is_running
    view.close()


@pytest.mark.parametrize("running", [False, True])
def test_drag_previews_without_seeking_and_commits_once_on_release(running):
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(START_TIME, clock.duration_minutes))
    if running:
        clock.play()
    seeks = []
    clock.time_changed.connect(seeks.append)
    view._scrubber.setSliderDown(True)
    for position in (4, 12, 23):
        view._scrubber.setSliderPosition(position)
    assert clock.t == 0
    assert not clock.is_running
    assert seeks == []
    assert view._position_label.text() == "01:55 / 24:00"
    view._scrubber.setSliderDown(False)
    assert clock.t == 23
    assert len(seeks) == 1
    assert clock.is_running == running
    clock.pause()
    view.close()


@pytest.mark.parametrize(
    "start", ["2026-10-11T07:00:00+00:00", "2026-10-12T07:00:00+00:00"]
)
def test_run_switch_during_drag_cancels_resume_and_old_position(start):
    clock = make_clock()
    view = ReplayBarView(clock)
    show(view, make_payload(START_TIME, clock.duration_minutes))
    clock.play()
    view._scrubber.setSliderDown(True)
    view._scrubber.setSliderPosition(100)
    end = (datetime.fromisoformat(start) + timedelta(minutes=50)).isoformat()
    clock.set_run(start, end)
    show(view, make_payload(clock.get_time(), clock.duration_minutes))
    assert not view._scrubber.isSliderDown()
    assert not clock.is_running
    assert clock.t == view._scrubber.value() == 0
    assert view._scrubber.maximum() == 9
    view.close()


def test_restart_preserves_paused_playback():
    clock = make_clock()
    view = ReplayBarView(clock)
    view.seek(12)
    view._restart_button.click()
    assert clock.t == 0
    assert not clock.is_running
    view.close()


def test_skip_end_seeks_to_last_step():
    clock = make_clock()
    view = ReplayBarView(clock)
    view.seek(5)
    view._skip_end_button.click()
    assert clock.t == clock.max_t - 1
    assert not clock.is_running
    view.close()


def test_speed_picker_drives_clock_and_external_updates_do_not_seek():
    clock = make_clock()
    view = ReplayBarView(clock)
    assert [view._speed_picker.itemText(i) for i in range(3)] == ["1×", "1.5×", "2×"]
    assert view._speed_picker.currentData() == 1
    times = []
    clock.time_changed.connect(times.append)
    view._speed_picker.setCurrentIndex(2)
    view._speed_picker.activated.emit(2)
    assert clock.playback_speed == 2
    assert not clock.is_running
    clock.set_playback_speed(1.5)
    assert view._speed_picker.currentData() == 1.5
    assert times == []
    view.play()
    view._scrubber.setSliderDown(True)
    view._scrubber.setSliderPosition(12)
    view._scrubber.setSliderDown(False)
    assert clock.is_running
    assert clock.t == 12
    assert clock.playback_speed == 1.5
    view.pause()
    view.close()


def test_speed_picker_disabled_without_a_run():
    view = ReplayBarView(make_clock(start_time=None))
    assert not view._speed_picker.isEnabled()
    view.close()
