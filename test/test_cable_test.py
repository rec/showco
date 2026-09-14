from __future__ import annotations

from unittest import mock

import numpy as np
import pytest
import tyro

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


def test_options_use_default_ranges() -> None:
    options = tyro.cli(cable_test.CableTestOptions, args=[])

    assert options.channels == '9-14'
    assert options.sends == '1-6'


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


def test_validate_pairs_rejects_different_lengths() -> None:
    with pytest.raises(ValueError, match='same length'):
        cable_test.validate_pairs([9, 10], [1])


def test_analyze_accepts_a_delayed_phase_shifted_tone() -> None:
    tone = cable_test.sine_wave(48_000)
    recorded = np.roll(tone, 317)

    result = cable_test.analyze(1, 9, tone, recorded, 48_000)

    assert result.passed
    assert result.similarity > 0.999
    assert result.level_ratio == pytest.approx(1.0, abs=0.001)


def test_analyze_rejects_noise() -> None:
    random = np.random.default_rng(1)
    tone = cable_test.sine_wave(48_000)
    recorded = random.normal(0, 0.05, tone.size).astype(np.float32)

    result = cable_test.analyze(1, 9, tone, recorded, 48_000)

    assert not result.passed
    assert result.similarity < 0.02


def test_cable_test_pauses_audio_and_restores_mixer_settings() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=True, message='ok')
    osc = FakeOsc()
    calls: list[tuple[str, int, int, int]] = []
    queried_after_pause = False

    def query_devices() -> list[dict[str, float | int | str]]:
        nonlocal queried_after_pause
        queried_after_pause = recs.pause_recording.call_count == 1
        return [audio_device()]

    def round_trip(
        device: str,
        source_channel: int,
        input_channel: int,
        sample_rate: int,
        tone: np.ndarray,
    ) -> np.ndarray:
        calls.append((device, source_channel, input_channel, sample_rate))
        return tone.copy()

    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=query_devices,
        round_trip=round_trip,
    )

    report = tester.run([9, 10], [1, 2])

    assert report.passed
    assert queried_after_pause
    assert calls == [('hw:2,0', 1, 9, 48_000), ('hw:2,0', 1, 10, 48_000)]
    recs.pause_recording.assert_called_once_with()
    recs.action.assert_called_once_with('resume_recording')
    assert osc.values['/lr/mix/on'] == 1
    assert all(
        value == 0.25 for path, value in osc.values.items() if path != '/lr/mix/on'
    )
    assert ('/headamp/09/phantom', 0) in osc.sets
    assert ('/headamp/09/gain', 1 / 6) in osc.sets


def test_cable_test_leaves_already_paused_recs_paused() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = False
    osc = FakeOsc()
    tester = cable_test.CableTester(
        recs,
        mixer(),
        osc_factory=lambda host, port: osc,
        query_devices=lambda: [audio_device()],
        round_trip=lambda device, source, channel, rate, tone: tone.copy(),
    )

    assert tester.run([9], [1]).passed
    recs.pause_recording.assert_called_once_with()
    recs.action.assert_not_called()


def test_cable_test_restores_state_and_resumes_after_audio_failure() -> None:
    recs = mock.Mock()
    recs.pause_recording.return_value = True
    recs.action.return_value = models.ActionResult(ok=True, message='ok')
    osc = FakeOsc()
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
    assert osc.values['/lr/mix/on'] == 1
    assert all(
        value == 0.25 for path, value in osc.values.items() if path != '/lr/mix/on'
    )


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
