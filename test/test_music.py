from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from showco.runtime import models, music
from showco.x18 import music as x18_music


def result() -> models.ActionResult:
    return models.ActionResult(ok=True, message='ok')


def controller() -> tuple[music.MusicController, mock.Mock, mock.Mock, mock.Mock]:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = result()
    player = mock.Mock()
    player.status.return_value = x18_music.MusicPlayerStatus()
    routing = mock.Mock()
    controller = music.MusicController(
        recs,
        player,
        routing,
        music.MusicConfig(
            setup_directory=Path('/music/setup'),
            teardown_directory=Path('/music/teardown'),
        ),
    )
    return controller, recs, player, routing


def test_setup_pauses_recording_starts_music_and_routes_it_to_main_lr() -> None:
    value, recs, player, routing = controller()

    result_value = value.setup()

    assert result_value.ok
    recs.pause_recording.assert_called_once_with()
    player.start.assert_called_once_with(Path('/music/setup'), False, 2.0)
    routing.enable.assert_called_once_with()
    assert value.status().mode == 'setup'


def test_setup_stops_streamo() -> None:
    value, _, _, _ = controller()
    streamo = mock.Mock()
    streamo.action.return_value = result()
    value.streamo = streamo

    value.setup()

    streamo.action.assert_called_once_with('stop')


def test_record_fades_music_before_starting_a_new_session() -> None:
    value, recs, player, routing = controller()

    result_value = value.record()

    assert result_value.ok
    player.stop.assert_called_once_with(2.0)
    routing.disable.assert_called_once_with()
    recs.action.assert_called_once_with('new_session')
    assert value.status().mode == 'record'


def test_record_starts_streamo_after_the_new_recording_session() -> None:
    value, recs, _, _ = controller()
    restart = mock.Mock(return_value=result())
    value.streamo = mock.Mock()
    value.streamo_restart = restart

    value.record()

    recs.action.assert_called_once_with('new_session')
    restart.assert_called_once_with()


def test_teardown_pauses_recording_stops_playback_and_starts_music() -> None:
    value, recs, player, routing = controller()

    result_value = value.teardown()

    assert result_value.ok
    recs.pause_recording.assert_called_once_with()
    assert recs.action.call_args_list == [mock.call('stop_playback')]
    player.start.assert_called_once_with(Path('/music/teardown'), False, 2.0)
    routing.enable.assert_called_once_with()
    assert value.status().mode == 'teardown'


def test_teardown_requests_credits_without_stopping_recs() -> None:
    value, recs, _, routing = controller()
    streamo = mock.Mock()
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(),
    )
    value.streamo = streamo
    value.mode = 'record'
    routing.capture_room.return_value = x18_music.RoomScene(
        master_fader=0.7, input_lr=[1] * 18
    )

    outcome = value.teardown()

    assert outcome.ok
    streamo.start_closing.assert_called_once()
    recs.pause_recording.assert_not_called()
    value.closing_stop.set()
    assert value.closing_thread is not None
    value.closing_thread.join(timeout=5)


def test_credits_keep_recs_running_until_broadcast_completes(tmp_path: Path) -> None:
    value, recs, player, routing = controller()
    streamo = mock.Mock()
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(),
    )
    value.streamo = streamo
    value.closing_state_path = tmp_path / 'closing.json'
    value.mode = 'record'
    scene = x18_music.RoomScene(master_fader=0.7, input_lr=[1] * 18)
    routing.capture_room.return_value = scene
    assert value.teardown().ok
    assert value.closing_record is not None
    operation_id = value.closing_record.operation_id
    assert value.closing_state_path.is_file()
    recs.pause_recording.assert_not_called()
    player.start.assert_not_called()

    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(
            operation_id=operation_id,
            state='running',
            phase='black',
            black_started_at=time.time(),
            black_at=time.time(),
        ),
    )
    deadline = time.monotonic() + 5
    while not routing.fade_main.called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert routing.fade_main.called
    recs.pause_recording.assert_not_called()
    player.start.assert_not_called()

    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(operation_id=operation_id, state='completed'),
    )
    while value.mode != 'teardown' and time.monotonic() < deadline:
        time.sleep(0.01)
    assert value.mode == 'teardown'
    recs.pause_recording.assert_called_once()
    player.start.assert_called_once_with(Path('/music/teardown'), False, 0)
    routing.isolate_instruments.assert_called_once_with(scene)
    routing.fade_main.assert_any_call(0.7, 2.0)
    assert streamo.start_closing.call_count == 1
    value.close()


def test_uncertain_closing_request_preserves_recording_and_operation(
    tmp_path: Path,
) -> None:
    value, recs, player, routing = controller()
    streamo = mock.Mock()
    streamo.start_closing.side_effect = TimeoutError('reply lost')
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='offline')
    )
    value.streamo = streamo
    value.closing_state_path = tmp_path / 'closing.json'
    value.mode = 'record'
    routing.capture_room.return_value = x18_music.RoomScene(
        master_fader=0.7, input_lr=[1] * 18
    )

    outcome = value.teardown()

    assert not outcome.ok
    assert 'uncertain' in outcome.message
    assert value.closing_record is not None
    assert value.closing_record.operation_id in value.closing_state_path.read_text()
    recs.pause_recording.assert_not_called()
    player.start.assert_not_called()
    value.close()


def test_failed_credits_leave_recs_recording_and_room_music_off() -> None:
    value, recs, player, routing = controller()
    streamo = mock.Mock()
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(),
    )
    value.streamo = streamo
    value.mode = 'record'
    routing.capture_room.return_value = x18_music.RoomScene(
        master_fader=0.7, input_lr=[1] * 18
    )
    assert value.teardown().ok
    assert value.closing_record is not None
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(
            operation_id=value.closing_record.operation_id,
            state='failed',
            error='encoder stopped',
        ),
    )
    deadline = time.monotonic() + 5
    while value.mode != 'fault' and time.monotonic() < deadline:
        time.sleep(0.01)
    assert value.mode == 'fault'
    assert 'encoder stopped' in str(value.status().error)
    recs.pause_recording.assert_not_called()
    player.start.assert_not_called()
    routing.disable.assert_called_once()
    value.close()


def test_showco_restart_observes_existing_closing_without_resending(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / 'closing.json'
    scene = x18_music.RoomScene(master_fader=0.7, input_lr=[1] * 18)
    record = music.ClosingRecord(
        operation_id='show-1', phase='credits-running', room_scene=scene
    )
    state_path.write_text(record.model_dump_json())
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = result()
    player = mock.Mock()
    player.status.return_value = x18_music.MusicPlayerStatus()
    routing = mock.Mock()
    streamo = mock.Mock()
    streamo.status.return_value = models.StreamoStatus(
        service=models.ServiceStatus(name='streamo', state='connected'),
        closing=models.ClosingStatus(
            operation_id='show-1',
            state='running',
            black_started_at=time.time(),
            black_at=time.time(),
        ),
    )
    value = music.MusicController(
        recs,
        player,
        routing,
        music.MusicConfig(),
        streamo=streamo,
        closing_state_path=state_path,
    )
    deadline = time.monotonic() + 5
    while not routing.fade_main.called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert routing.fade_main.called
    streamo.start_closing.assert_not_called()
    recs.pause_recording.assert_not_called()
    value.close()


def test_stop_fades_music_then_shuts_down_the_pi() -> None:
    value, _, player, routing = controller()
    poweroff = mock.Mock()
    value.poweroff = poweroff

    result_value = value.stop()

    assert result_value.ok
    player.stop.assert_called_once_with(2.0)
    routing.disable.assert_called_once_with()
    poweroff.assert_called_once_with()
    assert value.status().mode == 'stopped'


def test_failed_recording_command_mutes_returns_and_reports_completed_steps() -> None:
    value, recs, player, routing = controller()
    recs.action.return_value = models.ActionResult(ok=False, message='recs offline')

    with pytest.raises(
        ValueError, match='record failed at new recs session started: recs offline'
    ) as error:
        value.record()

    assert value.status().mode == 'fault'
    assert 'Completed: music stopped, music returns muted' in str(error.value)
    assert 'mute music returns' in str(error.value)
    assert value.status().error == str(error.value)
    routing.disable.assert_called()
    player.stop.assert_any_call(0)


def test_failed_setup_routing_mutes_music_returns() -> None:
    value, _, player, routing = controller()
    routing.enable.side_effect = TimeoutError('X18 did not reply')

    with pytest.raises(
        ValueError, match='setup failed at music returns enabled'
    ) as error:
        value.setup()

    assert 'Completed: streamO stopped, recs paused, setup music started' in str(
        error.value
    )
    routing.disable.assert_called_once_with()
    player.stop.assert_called_once_with(0)


def test_interrupted_music_transition_mutes_returns_and_reports_steps() -> None:
    value, recs, player, routing = controller()
    recs.pause_recording.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt, match='setup failed at recs paused') as error:
        value.setup()

    assert 'Completed: streamO stopped, mute music returns, stop music player' in str(
        error.value
    )
    assert value.status().mode == 'fault'
    routing.disable.assert_called_once_with()
    player.stop.assert_called_once_with(0)


class FakeOsc:
    def __init__(self) -> None:
        self.values: list[tuple[str, object]] = []
        self.applied: dict[str, object] = {}

    def __enter__(self) -> FakeOsc:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def query(self, path: str) -> object:
        return self.applied[path]

    def set(self, path: str, value: object) -> None:
        self.values.append((path, value))
        self.applied[path] = value


def test_x18_music_routing_uses_usb_returns_on_main_lr_and_mutes_them() -> None:
    osc = FakeOsc()
    routing = x18_music.X18MusicRouting(
        '10.0.0.18', 10_024, [17, 18], osc_factory=lambda host, port: osc
    )

    routing.enable()
    routing.disable()

    assert osc.values == [
        ('/config/chlink/17-18', 0),
        ('/ch/17/config/rtnsrc', 16),
        ('/ch/17/preamp/rtnsw', 1),
        ('/ch/17/mix/on', 1),
        ('/ch/17/mix/lr', 1),
        ('/ch/17/mix/fader', 0.4),
        ('/ch/18/config/rtnsrc', 17),
        ('/ch/18/preamp/rtnsw', 1),
        ('/ch/18/mix/on', 1),
        ('/ch/18/mix/lr', 1),
        ('/ch/18/mix/fader', 0.4),
        ('/ch/17/preamp/rtnsw', 0),
        ('/ch/17/mix/lr', 0),
        ('/ch/17/mix/on', 0),
        ('/ch/18/preamp/rtnsw', 0),
        ('/ch/18/mix/lr', 0),
        ('/ch/18/mix/on', 0),
    ]


def test_x18_room_scene_isolates_instruments_and_restores_main() -> None:
    osc = FakeOsc()
    osc.applied['/lr/mix/fader'] = 0.8
    for channel in range(1, 19):
        osc.applied[f'/ch/{channel:02}/mix/lr'] = int(channel % 2 == 0)
    routing = x18_music.X18MusicRouting(
        '10.0.0.18', 10_024, [17, 18], osc_factory=lambda host, port: osc
    )

    scene = routing.capture_room()
    with mock.patch('showco.x18.music.time.sleep'):
        routing.fade_main(0, 2)
    routing.isolate_instruments(scene)
    routing.restore_room(scene)

    assert scene.master_fader == 0.8
    assert scene.input_lr == [int(channel % 2 == 0) for channel in range(1, 19)]
    assert osc.applied['/lr/mix/fader'] == 0.8
    assert [
        osc.applied[f'/ch/{channel:02}/mix/lr'] for channel in range(1, 19)
    ] == scene.input_lr


def test_x18_music_routing_checks_readback_and_mutes_every_return() -> None:
    osc = FakeOsc()
    routing = x18_music.X18MusicRouting(
        '10.0.0.18', 10_024, [17, 18], osc_factory=lambda host, port: osc
    )
    observed = osc.query

    def stale_readback(path: str) -> object:
        if path == '/ch/17/preamp/rtnsw':
            return 1
        return observed(path)

    osc.query = stale_readback

    with pytest.raises(ValueError, match='could not be confirmed muted'):
        routing.disable()

    assert ('/ch/18/mix/on', 0) in osc.values
    assert len(osc.values) == 6


def test_music_skips_non_audio_files_and_reports_failed_decoder(
    tmp_path: Path,
) -> None:
    (tmp_path / 'notes.txt').write_text('set list')
    track = tmp_path / 'broken.flac'
    track.write_bytes(b'invalid audio')
    process = mock.Mock()
    process.stdout = io.BytesIO()
    process.poll.return_value = 1
    process.wait.return_value = 1
    stream = mock.Mock()
    factory = mock.Mock(return_value=process)
    player = x18_music.MusicPlayer(
        [17, 18],
        ['X18'],
        query_devices=lambda: [],
        stream_factory=lambda **kwargs: stream,
        process_factory=factory,
    )

    with mock.patch('showco.x18.music.find_audio_device', return_value=(0, 'X18')):
        player.start(tmp_path, False, 0)
    assert player.thread is not None
    player.thread.join(timeout=1)

    assert player.status().state == 'failed'
    assert 'Skipped broken.flac: ffmpeg exited with status 1' in (
        player.status().error or ''
    )
    assert factory.call_args.kwargs['stderr'] == subprocess.DEVNULL
    player.stop(0)


def test_music_stop_keeps_stream_owned_by_live_worker() -> None:
    stream = mock.Mock()
    thread = mock.Mock()
    thread.is_alive.return_value = True
    player = x18_music.MusicPlayer([17, 18], ['X18'])
    player.thread = thread
    player.stream = stream

    with pytest.raises(TimeoutError, match='did not stop'):
        player.stop(0)

    assert player.thread is thread
    assert player.stream is stream
    stream.close.assert_not_called()


def test_music_skips_unreadable_track_and_continues_playing(tmp_path: Path) -> None:
    for name in ('bad.wav', 'good.wav'):
        (tmp_path / name).write_bytes(b'audio')
    reading = threading.Event()
    release = threading.Event()

    class WaitingOutput:
        def read(self, size: int) -> bytes:
            reading.set()
            release.wait(timeout=2)
            return b''

    bad = mock.Mock(stdout=io.BytesIO())
    bad.poll.return_value = 1
    bad.wait.return_value = 1
    good = mock.Mock(stdout=WaitingOutput())
    good.poll.return_value = 0
    good.wait.return_value = 0
    factory = mock.Mock(side_effect=[bad, good])
    player = x18_music.MusicPlayer(
        [17, 18],
        ['X18'],
        query_devices=lambda: [],
        stream_factory=lambda **kwargs: mock.Mock(),
        process_factory=factory,
    )

    with mock.patch('showco.x18.music.find_audio_device', return_value=(0, 'X18')):
        player.start(tmp_path, False, 0)
    assert reading.wait(timeout=1)
    assert player.status().state == 'playing'
    assert player.status().track == tmp_path / 'good.wav'
    assert 'Skipped bad.wav' in (player.status().error or '')
    stopper = threading.Thread(target=player.stop, args=(0,))
    stopper.start()
    assert player.stop_requested.wait(timeout=1)
    release.set()
    stopper.join(timeout=1)
    assert not stopper.is_alive()


def test_music_configuration_overrides_directories_fade_and_playlist_order(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / 'music.toml'
    config_path.write_text(
        'setup_directory = "/music/open"\n'
        'teardown_directory = "/music/close"\n'
        'fade_seconds = 3.5\n'
        'shuffle = true\n'
        'source_channels = [15, 16]\n'
    )

    config = music.load_config(config_path)

    assert config == music.MusicConfig(
        setup_directory=Path('/music/open'),
        teardown_directory=Path('/music/close'),
        fade_seconds=3.5,
        shuffle=True,
        source_channels=[15, 16],
    )
