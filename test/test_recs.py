from __future__ import annotations

import json
import unittest
from threading import Event
from unittest import mock

from reccy.protocol import rpc
from recs.base.waveform import (
    WaveformBatchData,
    WaveformLayoutData,
    WaveformTrackData,
    WaveformTrackLayout,
)

from showco.runtime import models, recs
from showco.runtime.recs import (
    RecsClient,
    WaveformBridge,
    channel_levels,
    level_state,
    replace_track_name,
    stereo_tracks,
)
from showco.runtime.recs_control import RecsControlClient


class RecsTests(unittest.TestCase):
    def test_status_maps_complete_public_snapshot(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = status_snapshot()

        status = RecsClient(control=control, snapshot_cache_seconds=0).status()

        self.assertEqual(status.service.state, "connected")
        self.assertTrue(status.recording)
        self.assertTrue(status.paused)
        self.assertEqual(status.elapsed_seconds, 4.0)
        self.assertEqual(status.recorded_seconds, 3.0)
        self.assertEqual(status.file_count, 1)
        self.assertEqual(status.channels[0].name, "Lead")
        self.assertTrue(status.channels[0].on)
        self.assertEqual(status.errors[0].message, "disk almost full")
        assert status.disk is not None
        self.assertEqual(status.disk.free_bytes, 75)
        self.assertEqual(status.osc[0].name, "X18")
        self.assertEqual(status.midi[0].name, "FLOW 8")
        self.assertEqual(status.playback.state, "waiting")

    def test_status_snapshot_uses_short_lived_cache(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = status_snapshot()
        client = RecsClient(control=control)

        client.status()
        client.status()

        control.call.assert_called_once_with("status_snapshot", timeout=0.25)

    def test_unsupported_action_returns_failure(self) -> None:
        result = RecsClient().action("stop_recording")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "recs does not support stop_recording")

    def test_calibrate_uses_public_control(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = {
            "type": "calibrated",
            "measurements": {},
            "noise_floors": {},
        }

        result = RecsClient(control=control).calibrate()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs calibration succeeded")
        control.call.assert_called_once_with("calibrate")

    def test_calibrate_rejects_unexpected_response(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = "ok"

        result = RecsClient(control=control).calibrate()

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "recs did not send calibrated response")

    def test_calibrate_channel_uses_public_control(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = {
            "type": "calibrated",
            "measurements": {},
            "noise_floors": {},
        }

        result = RecsClient(control=control).calibrate("Mic", [1, 2])

        self.assertTrue(result.ok)
        control.call.assert_called_once_with("calibrate", {"channels": {"Mic": [1, 2]}})

    def test_set_track_name_uses_atomic_public_read_modify_write(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = [
            {"type": "track_names", "track_names": {"Mic": {"Old Name": 1}}},
            "ok",
        ]

        result = RecsClient(control=control).set_track_name(
            "Mic", "Old Name", "Lead Vocal"
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            control.call.call_args_list,
            [
                mock.call("get_track_names"),
                mock.call(
                    "set_track_names",
                    {"track_names": {"Mic": {"Lead Vocal": 1}}},
                ),
            ],
        )

    def test_set_stereo_sends_complete_track_payload(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = [status_snapshot(), track_names(), "ok"]

        result = RecsClient(control=control, snapshot_cache_seconds=0).set_stereo(
            "Mic", [1]
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            control.call.call_args_list[-1],
            mock.call(
                "set_tracks",
                {
                    "source": "Mic",
                    "tracks": [{"channels": [1, 2], "name": "Lead"}],
                },
            ),
        )

    def test_mutable_attributes_use_public_control(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = [
            {
                "type": "mutable_attributes_result",
                "mutable_attributes": ["recording.noise_floor"],
            },
            {
                "type": "cfg_value",
                "address": "recording.noise_floor",
                "value": 70.0,
            },
        ]

        attributes = RecsClient(control=control).mutable_attributes()

        self.assertEqual(
            attributes,
            [models.MutableAttribute(address="recording.noise_floor", value=70.0)],
        )

    def test_set_attr_requires_ok(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = "unexpected"

        result = RecsClient(control=control).set_attr("recording.noise_floor", 72.5)

        self.assertFalse(result.ok)
        control.call.assert_called_once_with(
            "set_cfg", {"address": "recording.noise_floor", "value": 72.5}
        )

    def test_actions_send_public_commands_and_parameters(self) -> None:
        for command in sorted(recs.ACTION_COMMANDS):
            with self.subTest(command=command):
                control = mock.Mock(spec=RecsControlClient)
                control.call.return_value = action_response(command)

                result = RecsClient(control=control).action(command)

                self.assertTrue(result.ok)
                control.call.assert_called_once_with(command)

    def test_action_forwards_nonempty_parameters(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = "ok"

        result = RecsClient(control=control).action("mark", label="test", unused="")

        self.assertTrue(result.ok)
        control.call.assert_called_once_with("mark", {"label": "test"})

    def test_play_starts_the_latest_session(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = [status_snapshot(), playback_state("playing")]

        result = RecsClient(control=control, snapshot_cache_seconds=0).play()

        self.assertTrue(result.ok)
        self.assertEqual(
            control.call.call_args_list,
            [
                mock.call("status_snapshot", timeout=0.25),
                mock.call("play_session", {"session": -1}),
            ],
        )

    def test_play_continues_paused_playback(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        snapshot = status_snapshot()
        snapshot["playback"] = playback_state("paused")
        control.call.side_effect = [snapshot, playback_state("playing")]

        result = RecsClient(control=control, snapshot_cache_seconds=0).play()

        self.assertTrue(result.ok)
        control.call.assert_has_calls(
            [
                mock.call("status_snapshot", timeout=0.25),
                mock.call("continue_playback"),
            ]
        )

    def test_playback_actions_invalidate_the_status_cache(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = [
            status_snapshot(),
            playback_state("waiting"),
            status_snapshot(),
        ]
        client = RecsClient(control=control)

        client.status()
        client.action("stop_playback")
        client.status()

        self.assertEqual(control.call.call_count, 3)

    def test_action_rejects_unexpected_success_response(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = {"type": "wrong"}

        result = RecsClient(control=control).action("card_replace")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "recs sent invalid card_replace response")

    def test_action_rejects_incomplete_typed_response(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = {"type": "capabilities_result"}

        result = RecsClient(control=control).action("capabilities")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "recs sent invalid capabilities response")

    def test_new_session_reports_the_new_session(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = action_response("new_session")

        result = RecsClient(control=control).action("new_session")

        self.assertTrue(result.ok)
        self.assertIn('"session_id": "session-2"', result.message)
        control.call.assert_called_once_with("new_session")

    def test_transport_failure_names_command(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = ConnectionError("refused")

        result = RecsClient(control=control).action("mark", label="test")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "recs mark failed: refused")

    def test_shutdown_uses_public_control(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = "ok"

        result = RecsClient(control=control).shutdown()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs shutdown requested")
        control.call.assert_called_once_with("shutdown")

    def test_waveform_bridge_keeps_current_layout_and_batches(self) -> None:
        bridge = WaveformBridge()
        layout = waveform_layout()
        batch = waveform_batch()

        bridge.receive(rpc.Event(name="waveform_layout", data=layout.model_dump()))
        bridge.receive(rpc.Event(name="waveform", data=batch.model_dump()))
        layouts, batches, changed = bridge.snapshot()

        self.assertEqual(layouts, [layout])
        self.assertEqual(batches, [batch])
        self.assertEqual(changed, 2)

    def test_waveform_bridge_discards_wrong_generation(self) -> None:
        bridge = WaveformBridge()
        bridge.receive(
            rpc.Event(name="waveform_layout", data=waveform_layout().model_dump())
        )
        data = waveform_batch().model_dump()
        data["generation"] = 2
        bridge.receive(rpc.Event(name="waveform", data=data))

        _, batches, _ = bridge.snapshot()

        self.assertEqual(batches, [])

    def test_waveform_bridge_bounds_batches_per_source(self) -> None:
        bridge = WaveformBridge()
        bridge.receive(
            rpc.Event(name="waveform_layout", data=waveform_layout().model_dump())
        )

        with mock.patch.object(recs, "MAX_WAVEFORM_BATCHES", 2):
            for sequence in range(3):
                data = waveform_batch().model_dump()
                data["sequence"] = sequence
                bridge.receive(rpc.Event(name="waveform", data=data))

        _, batches, _ = bridge.snapshot()

        self.assertEqual([b.sequence for b in batches], [1, 2])

    def test_waveform_bridge_marks_evicted_event_history_as_missed(self) -> None:
        with mock.patch.object(recs, "MAX_WAVEFORM_EVENTS", 2):
            bridge = WaveformBridge()
        for generation in range(1, 4):
            data = waveform_layout().model_dump()
            data["generation"] = generation
            bridge.receive(rpc.Event(name="waveform_layout", data=data))

        missed, events = bridge.events_since(0)

        self.assertTrue(missed)
        self.assertEqual(events, [])

    def test_invalid_waveform_event_reconnects_subscription(self) -> None:
        bridge = WaveformBridge()
        with mock.patch.object(recs.LOGGER, "error") as error:
            bridge.receive(rpc.Event(name="waveform_layout", data={}))

        self.assertTrue(bridge.reconnect.is_set())
        error.assert_called_once()

    def test_waveform_bridge_subscribes_and_unsubscribes(self) -> None:
        subscribed = Event()
        events: rpc.EventClient = mock.Mock(spec=rpc.EventClient)
        control = mock.Mock(spec=RecsControlClient)

        def call(
            command: str,
            parameters: dict[str, object] | None = None,
            *,
            timeout: float = 6.0,
        ) -> object:
            if command == "subscribe_waveforms":
                subscribed.set()
                return {"type": "waveform_subscription", "active": True}
            return {"type": "waveform_subscription", "active": False}

        control.call.side_effect = call
        bridge = WaveformBridge(
            event_client=lambda receive: events,
            control=control,
        )

        bridge.start()
        self.assertTrue(subscribed.wait(0.1))
        bridge.close()
        assert bridge.thread is not None

        self.assertEqual(
            control.call.call_args_list,
            [
                mock.call("subscribe_waveforms"),
                mock.call("unsubscribe_waveforms", timeout=1.0),
            ],
        )
        events.start.assert_called_once_with()
        events.close.assert_called_once_with()
        self.assertFalse(bridge.thread.is_alive())

    def test_status_changes_command_checks_successive_updated_at_values(self) -> None:
        command = recs.status_changes_command()

        self.assertIn('status="$HOME/.local/state/recs/status.json"', command)
        self.assertIn("sleep 4", command)
        self.assertIn("for sample in $(seq 3)", command)
        self.assertIn("current=$(updated_at)", command)
        self.assertIn('previous="$current"', command)

    def test_status_failure_summary_shows_recent_error_messages(self) -> None:
        summary = recs.status_failure_summary(
            json.dumps(
                {
                    "updated_at": 123.0,
                    "errors": ["first", {"message": "second"}, "third", "fourth"],
                }
            )
        )

        self.assertEqual(
            summary,
            "Recs status did not advance; updated_at=123.0\n"
            "Recent Recs errors:\n- second\n- third\n- fourth",
        )

    def test_level_state_uses_four_display_states(self) -> None:
        self.assertEqual(level_state(None), "silent")
        self.assertEqual(level_state(0.0), "silent")
        self.assertEqual(level_state(0.1), "present")
        self.assertEqual(level_state(0.5), "healthy")
        self.assertEqual(level_state(0.95), "clipping")

    def test_channel_levels_keep_device_context(self) -> None:
        channels = channel_levels(
            [
                {"device": "Mic"},
                {"channel": "1", "signal": 0.1},
                {"device": "X18"},
                {"channel": "2", "signal": 0.2},
            ]
        )

        self.assertEqual([c.device for c in channels], ["Mic", "X18"])

    def test_stereo_tracks_pairs_right_hand_mono_channel(self) -> None:
        tracks = stereo_tracks(
            [
                models.ChannelLevel(
                    name="1", state="healthy", device="Mic", channels=[1]
                ),
                models.ChannelLevel(
                    name="2", state="healthy", device="Mic", channels=[2]
                ),
                models.ChannelLevel(
                    name="3", state="healthy", device="Mic", channels=[3]
                ),
            ],
            "Mic",
            [1],
        )

        self.assertEqual(tracks, [[1, 2], [3]])

    def test_stereo_tracks_splits_stereo_channel(self) -> None:
        tracks = stereo_tracks(
            [
                models.ChannelLevel(
                    name="1-2", state="healthy", device="Mic", channels=[1, 2]
                )
            ],
            "Mic",
            [1, 2],
        )

        self.assertEqual(tracks, [[1], [2]])

    def test_replace_track_name_removes_old_name_for_channel(self) -> None:
        self.assertEqual(
            replace_track_name({"Mic": {"Old": 1, "Other": 2}}, "Mic", 1, "New"),
            {"Mic": {"Other": 2, "New": 1}},
        )


def status_snapshot() -> dict[str, object]:
    return {
        "type": "status_snapshot_result",
        "rows": [
            {"time": 4.0, "recorded": 3.0, "file_count": 1},
            {"device": "Mic"},
            {
                "channel": "Lead",
                "channels": [1],
                "signal": 0.5,
                "on": True,
            },
            {"channel": "Right", "channels": [2], "signal": 0.4, "on": True},
        ],
        "errors": [
            {
                "timestamp": "2026-09-07T12:00:00Z",
                "message": "disk almost full",
            }
        ],
        "recording": {"paused": True},
        "playback": playback_state("waiting"),
        "disk": disk_status(),
        "midi": [{"name": "FLOW 8", "state": "recording"}],
        "osc": [{"name": "X18", "state": "running", "path": "X18.jsonl"}],
    }


def disk_status() -> dict[str, object]:
    return {
        "path": "/recordings",
        "used_bytes": 25,
        "free_bytes": 75,
        "total_bytes": 100,
        "estimated_seconds_remaining": 3600.0,
        "alert_threshold": "10 GiB",
        "alert_active": True,
        "paused_for_disk_space": False,
    }


def track_names() -> dict[str, object]:
    return {"type": "track_names", "track_names": {"Mic": {"Lead": 1}}}


def playback_state(state: str) -> dict[str, object]:
    result: dict[str, object] = {"type": "playback_state", "state": state}
    if state != "waiting":
        result.update(
            {
                "session": -1,
                "path": "/recordings/session-2/recording.toml",
                "source": "Mic",
                "channel": "1-2",
                "output_channel": "1-2",
                "position_seconds": 10.0,
                "duration_seconds": 20.0,
            }
        )
    return result


def action_response(command: str) -> object:
    if command in recs.PLAYBACK_COMMANDS:
        return playback_state("waiting")
    if command == "capabilities":
        return {"type": "capabilities_result", "commands": [], "version": 7}
    if command == "card_replace":
        return {
            "type": "card_replace_started",
            "deadline": "2026-09-07T12:05:00Z",
            "old_mount": "/recordings",
            "old_uuid": "1234-ABCD",
        }
    if command == "disk_status":
        return {"type": "disk_status_result", **disk_status()}
    if command == "list_devices":
        return {
            "type": "devices",
            "devices": [
                {
                    "name": "Mic",
                    "channels": 2,
                    "sample_rate": 48_000,
                    "online": True,
                }
            ],
        }
    if command == "new_session":
        return {
            "type": "new_session_started",
            "session_id": "session-2",
            "session_directory": "/recordings/session-2",
            "previous_record_path": "/recordings/session-1/session-record.jsonl",
            "record_path": "/recordings/session-2/session-record.jsonl",
        }
    if command == "status_snapshot":
        return status_snapshot()
    return "ok"


def waveform_layout() -> WaveformLayoutData:
    return WaveformLayoutData(
        source="Mixer",
        generation=1,
        sample_rate=48_000,
        bucket_frames=960,
        tracks=[WaveformTrackLayout(channels=[1])],
    )


def waveform_batch() -> WaveformBatchData:
    return WaveformBatchData(
        source="Mixer",
        generation=1,
        sequence=0,
        sample_rate=48_000,
        bucket_frames=960,
        start_frame=0,
        start_timestamp=1.0,
        present=[True],
        tracks=[WaveformTrackData(channels=[1], minimum=[[-0.5]], maximum=[[0.5]])],
    )


if __name__ == "__main__":
    unittest.main()
