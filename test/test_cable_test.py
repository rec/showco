from __future__ import annotations

import wave
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from unittest import mock

import numpy as np
import pytest
from pytest_regressions.data_regression import DataRegressionFixture

from showco.runtime import models
from showco.runtime.mixer import MixerOscSpec, MixerSpec
from showco.x18 import cable_test


class FakeOsc:
    def __init__(self) -> None:
        self.values: dict[str, str | int | float | bool] = {}
        self.sets: list[tuple[str, str | int | float | bool]] = []

    def __enter__(self) -> FakeOsc:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def query(self, path: str) -> str | int | float | bool:
        return self.values.setdefault(path, 0.25)

    def set(self, path: str, value: str | int | float | bool) -> None:
        self.values[path] = value
        self.sets.append((path, value))


def test_x18_osc_writes_do_not_wait_for_a_reply() -> None:
    transport = mock.Mock()
    with mock.patch('showco.x18.cable_test.socket.socket', return_value=transport):
        client = cable_test.X18OscClient('10.0.0.18', 10_024)

    client.set('/lr/mix/on', 0)

    transport.send.assert_called_once()
    transport.recv.assert_not_called()


@pytest.mark.parametrize(
    ('value', 'minimum', 'maximum', 'expected'),
    [
        ('9-14', 1, 18, [9, 10, 11, 12, 13, 14]),
        ('3', 1, 6, [3]),
    ],
)
def test_parse_range(
    value: str, minimum: int, maximum: int, expected: list[int]
) -> None:
    assert cable_test.parse_range(value, minimum, maximum, 'values') == expected


@pytest.mark.parametrize('value', ['0-3', '3-2', '1-7', '1-2-3', 'x'])
def test_parse_range_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        cable_test.parse_range(value, 1, 6, 'sends')


def test_validate_channels_and_sends_are_independent() -> None:
    cable_test.validate_channels([9, 10])
    cable_test.validate_sends([1])


@pytest.mark.parametrize('seconds', [0.5, 0.0, -1.0, 300.1, float('inf'), float('nan')])
def test_cable_test_duration_requires_a_finite_analyzable_signal(
    seconds: float,
) -> None:
    with pytest.raises(ValueError, match='duration'):
        cable_test.validate_duration(seconds)


def test_cable_test_rejects_long_duration_before_pausing_recording() -> None:
    recs = mock.Mock()
    tester = cable_test.CableTester(recs, mixer())

    with pytest.raises(ValueError, match='at most 300 seconds'):
        tester.run([9], [1], duration_seconds=301)

    recs.pause_recording.assert_not_called()


@pytest.mark.parametrize('last', [16, 17, 18])
def test_full_input_range_is_rejected_before_recording_is_paused(last: int) -> None:
    recs = mock.Mock()
    tester = cable_test.CableTester(recs, mixer())
    with pytest.raises(ValueError, match='unused'):
        tester.run(list(range(1, last + 1)), [1])
    recs.pause_recording.assert_not_called()


def test_osc_unrelated_replies_cannot_extend_the_query_deadline() -> None:
    transport = mock.Mock()
    transport.recv.return_value = cable_test.encode_message('/other', [1])
    with (
        mock.patch.object(cable_test.socket, 'socket', return_value=transport),
        mock.patch.object(cable_test, 'monotonic', side_effect=[0, 0, 0.5, 2]),
    ):
        client = cable_test.X18OscClient('10.0.0.18', 10024, timeout_seconds=2)
        with pytest.raises(TimeoutError, match='within 2s'):
            client.query('/wanted')
    assert transport.recv.call_count == 2


@pytest.mark.parametrize('failure', ['startup', 'playback_timeout', 'capture_timeout'])
def test_audio_failure_reaps_the_capture_process(failure: str) -> None:
    recorder = mock.Mock()
    recorder.poll.return_value = None
    recorder.communicate.return_value = ('', '')
    timeout = cable_test.subprocess.TimeoutExpired('audio', 8)
    playback = mock.Mock(return_value=mock.Mock(returncode=0))
    if failure == 'startup':
        playback.side_effect = FileNotFoundError('aplay missing')
    elif failure == 'playback_timeout':
        playback.side_effect = timeout
    else:
        recorder.communicate.side_effect = [timeout, ('', '')]
    with (
        mock.patch.object(cable_test.subprocess, 'Popen', return_value=recorder),
        mock.patch.object(cable_test.subprocess, 'run', playback),
        pytest.raises((FileNotFoundError, TimeoutError)),
    ):
        with cable_test.audio_round_trip('hw:2,0', 1, 48000, np.zeros(48000)):
            pass
    recorder.kill.assert_called_once_with()
    assert recorder.communicate.call_args.kwargs['timeout'] == 5
    assert playback.call_args.kwargs['timeout'] == 6


def test_long_cable_test_rejects_insufficient_temporary_space() -> None:
    tone = np.zeros(48_000, dtype=np.float32)
    with (
        mock.patch.object(
            cable_test.shutil, 'disk_usage', return_value=mock.Mock(free=0)
        ),
        mock.patch.object(cable_test.subprocess, 'Popen') as recorder,
        pytest.raises(OSError, match='needs .* free'),
        cable_test.audio_round_trip('hw:2,0', 1, 48_000, tone),
    ):
        pytest.fail('insufficient space should prevent audio processes')
    recorder.assert_not_called()


def test_audio_round_trip_decodes_one_second_without_loading_all_channels(
    tmp_path: Path,
) -> None:
    tone = cable_test.sine_wave(48_000, 1.0)
    samples = np.zeros((tone.size, 18), dtype=np.int32)
    samples[:, 8] = np.rint(tone * ((1 << 23) - 1)).astype(np.int32)
    recorder = mock.Mock(returncode=0)
    recorder.poll.return_value = 0
    recorder.communicate.return_value = ('', '')

    def start_recording(command: list[str], **kwargs: object) -> mock.Mock:
        Path(command[-1]).write_bytes(cable_test.encode_s24le(samples))
        return recorder

    playback = mock.Mock(return_value=mock.Mock(returncode=0, stderr=''))
    with (
        mock.patch.object(cable_test.subprocess, 'Popen', side_effect=start_recording),
        mock.patch.object(cable_test.subprocess, 'run', playback),
        cable_test.audio_round_trip('hw:2,0', 1, 48_000, tone) as recorded,
    ):
        assert isinstance(recorded, np.memmap)
        assert recorded.shape == (48_000, 18)
        assert cable_test.analyze(9, tone, recorded[:, 8], 48_000).passed
        wav_path = tmp_path / 'cable-test-capture.wav'
        with wave.open(str(wav_path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(np.rint(recorded[:, 8] * 32767).astype('<i2').tobytes())
        assert wav_path.stat().st_size > 48_000 * 2
    assert playback.call_args.args[0][-1].endswith('output.raw')


def test_analyze_accepts_a_delayed_phase_shifted_tone() -> None:
    tone = cable_test.sine_wave(48_000)
    recorded = np.roll(tone, 317)

    result = cable_test.analyze(9, tone, recorded, 48_000)

    assert result.passed
    assert result.similarity > 0.999
    assert result.level_ratio == pytest.approx(1.0, abs=0.001)


def test_analyze_rejects_noise() -> None:
    random = np.random.default_rng(1)
    tone = cable_test.sine_wave(48_000)
    recorded = random.normal(0, 0.05, tone.size).astype(np.float32)

    result = cable_test.analyze(9, tone, recorded, 48_000)

    assert not result.passed
    assert result.similarity < 0.02
    assert 'distorted signal' in result.line()


def test_analyze_reports_no_signal() -> None:
    tone = cable_test.sine_wave(48_000)

    result = cable_test.analyze(9, tone, np.zeros_like(tone), 48_000)

    assert not result.passed
    assert result.line() == 'FAIL: channel 9: no signal'


def test_analyze_reports_an_intermittent_signal_with_its_time_range() -> None:
    tone = cable_test.sine_wave(48_000)
    recorded = tone.copy()
    recorded[48_000:72_000] = 0

    result = cable_test.analyze(9, tone, recorded, 48_000)

    assert not result.passed
    assert result.signal_gaps == [
        cable_test.CableSignalGap(start_seconds=1.0, end_seconds=1.5)
    ]
    assert result.line() == 'FAIL: channel 9: intermittent signal at 1.00-1.50s'


def test_audio_classification_wav_regression(
    tmp_path: Path, data_regression: DataRegressionFixture
) -> None:
    tone = cable_test.sine_wave(48000)
    signals = np.column_stack(
        [
            tone,
            tone * 0.5,
            np.clip(tone * 20, -1, 1),
            np.zeros_like(tone),
            np.clip(tone, -0.025, 0.025),
        ]
    )
    path = tmp_path / 'cable-signals.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(signals.shape[1])
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(np.rint(signals * 32767).astype('<i2').tobytes())
    with wave.open(str(path)) as source:
        recorded = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2')
    recorded = recorded.reshape(-1, signals.shape[1]).astype(np.float32) / 32767
    results = [cable_test.analyze(i + 1, tone, recorded[:, i], 48000) for i in range(5)]
    data_regression.check([{'passed': r.passed, 'message': r.line()} for r in results])


@pytest.mark.parametrize('master_on', [0, 1])
def test_cable_test_pauses_audio_and_restores_mixer_settings(master_on: int) -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=True, message='ok')
    osc = FakeOsc()
    osc.values['/lr/mix/on'] = master_on
    calls: list[tuple[str, int, int]] = []
    queried_after_pause = False

    def query_devices() -> list[dict[str, float | int | str]]:
        nonlocal queried_after_pause
        queried_after_pause = recs.pause_recording.call_count == 1
        return [audio_device()]

    def round_trip(
        device: str,
        source_channel: int,
        sample_rate: int,
        tone: np.ndarray,
    ) -> AbstractContextManager[np.ndarray]:
        calls.append((device, source_channel, sample_rate))
        assert all(
            osc.values[f'/ch/{source_channel:02}/mix/{s:02}/level']
            == cable_test.UNITY_FADER
            for s in [1, 2, 3]
        )
        return nullcontext(np.broadcast_to(tone[:, np.newaxis], (tone.size, 18)).copy())

    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=query_devices,
        round_trip=round_trip,
    )

    report = tester.run([9, 10], [1, 2, 3])

    assert report.passed
    assert queried_after_pause
    assert calls == [('hw:2,0', 1, 48_000)]
    recs.pause_recording.assert_called_once_with()
    recs.action.assert_called_once_with('resume_recording')
    assert osc.values['/lr/mix/on'] == master_on
    assert all(
        value == 0.25 for path, value in osc.values.items() if path != '/lr/mix/on'
    )
    assert ('/headamp/09/phantom', 0) in osc.sets
    assert ('/headamp/09/gain', 1 / 6) in osc.sets
    assert ('/ch/01/mix/01/level', cable_test.UNITY_FADER) in osc.sets


def test_cable_test_reports_only_failed_channels() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = False
    tone = cable_test.sine_wave(48_000)
    recorded = np.broadcast_to(tone[:, np.newaxis], (tone.size, 18)).copy()
    recorded[:, 9] = 0
    failures: list[cable_test.CableChannelResult] = []
    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: FakeOsc(),
        query_devices=lambda: [audio_device()],
        round_trip=lambda device, source, rate, tone: nullcontext(recorded),
    )

    report = tester.run([9, 10], [1, 2], failures.append)

    assert report.message().splitlines() == [
        'Cable test: 1/2 passed',
        'FAIL: channel 10: no signal',
    ]
    assert [failure.channel for failure in failures] == [10]


def test_cable_test_uses_configured_duration() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = False
    frames: list[int] = []

    def round_trip(
        device: str, source_channel: int, sample_rate: int, tone: np.ndarray
    ) -> AbstractContextManager[np.ndarray]:
        frames.append(tone.size)
        return nullcontext(np.broadcast_to(tone[:, np.newaxis], (tone.size, 18)).copy())

    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: FakeOsc(),
        query_devices=lambda: [audio_device()],
        round_trip=round_trip,
    )

    assert tester.run([9], [1], duration_seconds=5.0).passed
    assert frames == [240_000]


def test_cable_test_leaves_already_paused_recs_paused() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = False
    osc = FakeOsc()
    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=lambda: [audio_device()],
        round_trip=lambda device, source, rate, tone: nullcontext(
            np.broadcast_to(tone[:, np.newaxis], (tone.size, 18)).copy()
        ),
    )

    assert tester.run([9], [1]).passed
    recs.pause_recording.assert_called_once_with()
    recs.action.assert_not_called()


@pytest.mark.parametrize('master_on', [0, 1])
def test_cable_test_restores_state_and_resumes_after_audio_failure(
    master_on: int,
) -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=True, message='ok')
    osc = FakeOsc()
    osc.values['/lr/mix/on'] = master_on
    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=lambda: [audio_device()],
        round_trip=mock.Mock(side_effect=OSError('audio failed')),
    )

    with pytest.raises(OSError, match='audio failed'):
        tester.run([9], [1])

    recs.pause_recording.assert_called_once_with()
    recs.action.assert_called_once_with('resume_recording')
    assert osc.values['/lr/mix/on'] == master_on
    assert all(
        value == 0.25 for path, value in osc.values.items() if path != '/lr/mix/on'
    )


def test_cable_test_reports_audio_mixer_and_recs_cleanup_failures() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=False, message='recs offline')
    osc = FakeOsc()
    original_set = osc.set

    def failing_restore(path: str, value: str | int | float | bool) -> None:
        if path == '/lr/mix/on' and value == 0.25:
            raise OSError('X18 offline')
        original_set(path, value)

    osc.set = failing_restore
    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=lambda: [audio_device()],
        round_trip=mock.Mock(side_effect=OSError('audio failed')),
    )

    with pytest.raises(ValueError) as error:
        tester.run([9], [1])

    assert 'audio failed' in str(error.value)
    assert 'mixer restore failed: X18 offline' in str(error.value)
    assert 'recs resume failed: could not resume recording: recs offline' in str(
        error.value
    )


@pytest.mark.parametrize('master_on', [0, 1])
def test_routing_restores_master_after_setup_failure(master_on: int) -> None:
    osc = FakeOsc()
    osc.values['/lr/mix/on'] = master_on
    query = osc.query

    def failing_query(path: str) -> str | int | float | bool:
        if path == '/config/chlink/1-2':
            raise TimeoutError('setup failed')
        return query(path)

    with (
        mock.patch.object(osc, 'query', side_effect=failing_query),
        pytest.raises(TimeoutError, match='setup failed'),
        cable_test.X18TestRouting(osc, 1, [9], [1]),
    ):
        pytest.fail('setup should not complete')

    assert osc.values['/lr/mix/on'] == master_on


def test_cable_test_resumes_after_audio_device_discovery_failure() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=True, message='ok')
    tester = cable_test.CableTester(
        recs,
        mixer(),
        query_devices=list,
    )

    with pytest.raises(ValueError, match='not found'):
        tester.run([9], [1])

    recs.pause_recording.assert_called_once_with()
    recs.action.assert_called_once_with('resume_recording')


def test_find_audio_device_uses_alsa_hardware_address() -> None:
    devices = [
        {**audio_device(), 'max_output_channels': 2},
        {**audio_device(), 'name': 'Other'},
    ]

    assert cable_test.find_audio_device(devices, ['X18', 'XR18'], 1) == (
        'hw:2,0',
        48_000,
    )


def test_find_audio_device_reports_missing_requested_channels() -> None:
    devices = [{**audio_device(), 'max_output_channels': 2}]

    with pytest.raises(ValueError, match='at least 3 output'):
        cable_test.find_audio_device(devices, ['X18', 'XR18'], 3)


def test_s24le_round_trip() -> None:
    samples = np.array([[0, 8_388_607, -8_388_608] + [0] * 15], dtype=np.int32)

    decoded = cable_test.decode_s24le(cable_test.encode_s24le(samples))

    assert decoded[0, :3] == pytest.approx([0.0, 1.0 - 1 / (1 << 23), -1.0])


def mixer() -> MixerSpec:
    return MixerSpec(
        name='X18',
        audio_device_names=['X18', 'XR18'],
        ip_address='10.0.0.18',
        port=10_024,
        osc=MixerOscSpec(
            host='10.0.0.18',
            port=10_024,
            subscription_path='/xremote',
            resubscribe_period=10,
        ),
    )


def audio_device() -> dict[str, float | int | str]:
    return {
        'name': 'X18: USB Audio (hw:2,0)',
        'max_input_channels': 18,
        'max_output_channels': 18,
        'default_samplerate': 48_000.0,
    }
