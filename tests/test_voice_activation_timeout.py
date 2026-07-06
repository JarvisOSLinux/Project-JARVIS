"""Tests for the post-wake-word silence timeout (Project-JARVIS#137) and the
WOKEN/CAPTURING/discard state broadcasts layered on top for the formal voice
state machine (Project-JARVIS#141).

``iter_results()`` never yields during pure silence, so a plain ``for`` loop
over it can't detect "the user said nothing" -- these tests cover the fix in
``process_voice_command_inject``, which polls via ``read()`` instead so a
deadline can actually be checked between results.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jarvis.config import Config
from jarvis.core.voice_state import VoiceState
from jarvis.runtime import voice_activation_thread as vat


def _make_app(stt_read_side_effect, is_running=True):
    stt = Mock()
    stt.read.side_effect = stt_read_side_effect
    stt.is_running.return_value = is_running

    activation = Mock()
    events = Mock()
    events.call_soon_threadsafe = Mock()

    voice_manager = SimpleNamespace(stt=stt, activation=activation)
    return (
        SimpleNamespace(
            voice_manager=voice_manager,
            events=events,
            _running=True,
            _gui_clients=set(),  # run_voice_activation also broadcasts
            # wake_word_detected via the real broadcast_to_gui_clients;
            # empty set makes it a safe no-op for these tests.
        ),
        stt,
        activation,
        events,
    )


def _run_and_record_states(action, events_mock):
    """Run `action` (which schedules state broadcasts via events.call_soon_
    threadsafe) with io.set_gui_state faked out, then execute each scheduled
    callback and return the (state, meta) pairs it recorded.

    _broadcast_gui_state imports set_gui_state locally at call time, so the
    patch must be active while `action` itself runs -- not just while the
    scheduled callbacks are later replayed.
    """
    calls = []

    async def fake_set_gui_state(app, state, meta=None):
        calls.append((state, meta))

    with patch("jarvis.runtime.io.set_gui_state", new=fake_set_gui_state):
        action()

        loop = asyncio.new_event_loop()
        try:
            for call in events_mock.call_soon_threadsafe.call_args_list:
                cb = call.args[0]
                loop.run_until_complete(_run_callback(cb))
        finally:
            loop.close()
    return calls


async def _run_callback(cb):
    task = cb()
    if task is not None:
        await task


@pytest.mark.unit
class TestSilenceTimeout:
    def test_no_speech_within_timeout_returns_without_processing(self):
        app, stt, activation, events = _make_app(stt_read_side_effect=lambda **_: None)
        clock = iter([0.0, 0.5, 2.0, 4.5])

        with (
            patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0),
            patch.object(vat.time, "monotonic", side_effect=lambda: next(clock)),
        ):
            vat.process_voice_command_inject(app, Mock())

        events.inject_user_input.assert_not_called()
        stt.stop.assert_called_once()
        activation.start_listening.assert_called()

    def test_timeout_disabled_when_zero(self):
        app, stt, activation, events = _make_app(
            stt_read_side_effect=[None, None, ("hi", True)]
        )

        with patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 0):
            vat.process_voice_command_inject(app, Mock())

        events.inject_user_input.assert_called_once_with("hi")

    def test_speech_finishing_before_timeout_is_processed(self):
        app, stt, activation, events = _make_app(
            stt_read_side_effect=[("hel", False), ("hello world", True)]
        )

        with patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0):
            vat.process_voice_command_inject(app, Mock())

        events.inject_user_input.assert_called_once_with("hello world")
        stt.stop.assert_called_once()
        activation.start_listening.assert_called()

    def test_timeout_disabled_once_speech_has_started(self):
        # Speech starts immediately, then several silent polls follow.
        # monotonic() must only be consulted while waiting for speech to
        # start -- once it has, the deadline check must not run again, no
        # matter how much (real) time the rest of the utterance takes.
        app, stt, activation, events = _make_app(
            stt_read_side_effect=[("hel", False)] + [None] * 5 + [("hello world", True)]
        )
        clock_calls = [0.0, 0.1]

        def fake_monotonic():
            if not clock_calls:
                raise AssertionError(
                    "deadline should not be rechecked once speech_started is True"
                )
            return clock_calls.pop(0)

        with (
            patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0),
            patch.object(vat.time, "monotonic", side_effect=fake_monotonic),
        ):
            vat.process_voice_command_inject(app, Mock())

        events.inject_user_input.assert_called_once_with("hello world")

    def test_stops_early_if_stt_no_longer_running(self):
        app, stt, activation, events = _make_app(
            stt_read_side_effect=lambda **_: None, is_running=False
        )

        with patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0):
            vat.process_voice_command_inject(app, Mock())

        stt.read.assert_not_called()
        events.inject_user_input.assert_not_called()

    def test_activation_restarted_only_when_app_running(self):
        app, stt, activation, events = _make_app(stt_read_side_effect=[("hi", True)])
        app._running = False

        with patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0):
            vat.process_voice_command_inject(app, Mock())

        activation.start_listening.assert_not_called()


@pytest.mark.unit
class TestVoiceStateBroadcasts:
    def test_capturing_then_idle_with_no_meta_on_success(self):
        app, stt, activation, events = _make_app(stt_read_side_effect=[("hi", True)])

        def action():
            with patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0):
                vat.process_voice_command_inject(app, Mock())

        states = _run_and_record_states(action, events)
        assert states == [
            (VoiceState.CAPTURING, None),
            (VoiceState.IDLE, None),
        ]

    def test_idle_carries_discard_meta_on_timeout(self):
        app, stt, activation, events = _make_app(stt_read_side_effect=lambda **_: None)
        clock = iter([0.0, 0.5, 4.5])

        def action():
            with (
                patch.object(Config, "VOICE_ACTIVATION_TIMEOUT", 4.0),
                patch.object(vat.time, "monotonic", side_effect=lambda: next(clock)),
            ):
                vat.process_voice_command_inject(app, Mock())

        states = _run_and_record_states(action, events)
        assert states[0] == (VoiceState.CAPTURING, None)
        assert states[1][0] == VoiceState.IDLE
        assert states[1][1]["reason"] == "discard"

    def test_woken_broadcast_before_capture_starts(self):
        vm = SimpleNamespace(stt=Mock(), activation=Mock(), _wake_word_detected=False)
        vm.stt.is_running.return_value = False  # end the capture loop immediately

        def fake_start_listening():
            # run_voice_activation resets _wake_word_detected=False on entry,
            # then wires activation and starts listening -- simulate the
            # (mocked-out) wake-word engine firing right after that.
            vm._wake_word_detected = True
            return True

        vm.activation.start_listening.side_effect = fake_start_listening
        events = Mock()
        events.call_soon_threadsafe = Mock()
        app = SimpleNamespace(
            voice_manager=vm, events=events, _running=True, _gui_clients=set()
        )

        def stop_after_one_pass(_seconds):
            app._running = False  # exit run_voice_activation's while loop next check

        def action():
            with patch.object(vat.time, "sleep", side_effect=stop_after_one_pass):
                vat.run_voice_activation(app, Mock())

        states = _run_and_record_states(action, events)
        assert states[0] == (VoiceState.WOKEN, None)


@pytest.mark.unit
class TestBroadcastGuiState:
    @pytest.mark.asyncio
    async def test_schedules_set_gui_state_onto_the_loop(self):
        app = SimpleNamespace(events=Mock())
        loop = asyncio.get_running_loop()
        app.events.call_soon_threadsafe = lambda cb: loop.call_soon(cb)

        with patch("jarvis.runtime.io.set_gui_state") as mock_set_state:
            mock_set_state.side_effect = lambda *a, **kw: _noop()
            vat._broadcast_gui_state(app, VoiceState.CAPTURING)
            await asyncio.sleep(0)

        mock_set_state.assert_called_once_with(app, VoiceState.CAPTURING, None)

    def test_noop_when_app_has_no_events(self):
        app = SimpleNamespace(events=None)
        vat._broadcast_gui_state(app, VoiceState.IDLE)  # must not raise


async def _noop():
    return None
