from __future__ import annotations

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


def test_teardown_stops_streamo() -> None:
    value, _, _, _ = controller()
    streamo = mock.Mock()
    streamo.action.return_value = result()
    value.streamo = streamo

    value.teardown()

    streamo.action.assert_called_once_with('stop')


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


def test_failed_recording_command_leaves_mode_unchanged() -> None:
    value, recs, _, _ = controller()
    recs.action.return_value = models.ActionResult(ok=False, message='recs offline')

    with pytest.raises(ValueError, match='recs offline'):
        value.record()

    assert value.status().mode == 'stopped'


class FakeOsc:
    def __init__(self) -> None:
        self.values: list[tuple[str, object]] = []

    def __enter__(self) -> FakeOsc:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def query(self, path: str) -> str:
        return ''

    def set(self, path: str, value: object) -> None:
        self.values.append((path, value))


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
