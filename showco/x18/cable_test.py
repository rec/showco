from __future__ import annotations

import re
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from math import isfinite, pi
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated, Protocol, cast

import numpy as np
import sounddevice
import tyro
from pydantic import BaseModel, Field
from reccy.device import DeviceDict
from recs.osc.codec import decode_packet, encode_message

from ..deployment import machine_role
from ..runtime import models
from ..runtime.mixer import MixerSpec, load_mixer_specs
from ..runtime.recs import RecsClient

DEFAULT_CHANNELS = '9-14'
DEFAULT_SENDS = '1-6'
TONE_FREQUENCY = 110.0
TONE_SECONDS = 3.0
ANALYSIS_MARGIN_SECONDS = 0.25
TONE_AMPLITUDE = 0.1
MINIMUM_SIGNAL_RMS = TONE_AMPLITUDE * 0.01
MINIMUM_SIMILARITY = 0.98
MINIMUM_LEVEL_RATIO = 0.7
MAXIMUM_LEVEL_RATIO = 1.3
OSC_TIMEOUT_SECONDS = 1.0
UNITY_FADER = 0.75
X18_CHANNELS = 18
X18_SAMPLE_FORMAT = 'S24_3LE'


class CableTestOptions(BaseModel, frozen=True):
    channels: Annotated[str, tyro.conf.Positional] = DEFAULT_CHANNELS
    sends: Annotated[str, tyro.conf.Positional] = DEFAULT_SENDS
    duration_seconds: Annotated[
        float, Field(gt=ANALYSIS_MARGIN_SECONDS * 2, allow_inf_nan=False)
    ] = TONE_SECONDS
    mixers_config: Path = Path.home() / '.config/showco/mixers.toml'


class CableChannelResult(BaseModel, frozen=True):
    channel: int
    passed: bool
    similarity: float
    level_ratio: float
    rms: float
    peak: float

    def line(self) -> str:
        if not self.passed and self.rms < MINIMUM_SIGNAL_RMS:
            return f'FAIL: channel {self.channel}: no signal'
        if self.passed:
            state = 'PASS'
        elif self.peak >= 0.99:
            state = 'FAIL: clipping'
        elif self.similarity < MINIMUM_SIMILARITY:
            state = 'FAIL: distorted signal'
        else:
            state = 'FAIL: incorrect level'
        return (
            f'{state}: channel {self.channel}: '
            f'{self.similarity:.1%} tone match, {self.level_ratio:.1%} level'
        )


class CableTestReport(BaseModel, frozen=True):
    results: list[CableChannelResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def message(self) -> str:
        passed = sum(r.passed for r in self.results)
        failures = [r.line() for r in self.results if not r.passed]
        return '\n'.join(
            [f'Cable test: {passed}/{len(self.results)} passed'] + failures
        )


class OscControl(Protocol):
    def query(self, path: str) -> str | int | float | bool: ...

    def set(self, path: str, value: str | int | float | bool) -> None: ...


class X18OscClient:
    def __init__(
        self, host: str, port: int, timeout_seconds: float = OSC_TIMEOUT_SECONDS
    ) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.timeout_seconds = timeout_seconds
        self.socket.connect((host, port))
        self.socket.settimeout(timeout_seconds)

    def __enter__(self) -> X18OscClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.socket.close()

    def query(self, path: str) -> str | int | float | bool:
        try:
            return self._request(path, [])
        except TimeoutError:
            raise TimeoutError(
                f'X18 did not reply to {path} within {self.timeout_seconds}s'
            ) from None

    def set(self, path: str, value: str | int | float | bool) -> None:
        self.socket.send(encode_message(path, [value]))

    def _request(
        self, path: str, arguments: list[str | int | float | bool]
    ) -> str | int | float | bool:
        self.socket.send(encode_message(path, arguments))
        deadline = monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.socket.settimeout(remaining)
            for message in decode_packet(self.socket.recv(65_535)):
                if message.get('path') != path:
                    continue
                values = message.get('args')
                if not isinstance(values, list) or not values:
                    raise ValueError(f'X18 returned no value for {path}')
                value = values[0]
                if isinstance(value, str | int | float | bool):
                    return value
                raise ValueError(f'X18 returned an invalid value for {path}')


class X18TestRouting:
    def __init__(
        self,
        osc: OscControl,
        source_channel: int,
        channels: list[int],
        sends: list[int],
    ) -> None:
        self.osc = osc
        self.source_channel = source_channel
        self.channels = channels
        self.sends = sends
        self.saved: list[tuple[str, str | int | float | bool]] = []

    def __enter__(self) -> X18TestRouting:
        try:
            self._change('/lr/mix/on', 0)
            source_pair = self.source_channel - (self.source_channel + 1) % 2
            self._change(f'/config/chlink/{source_pair}-{source_pair + 1}', 0)
            for pair in sorted({send - (send + 1) % 2 for send in self.sends}):
                self._change(f'/config/buslink/{pair}-{pair + 1}', 0)
            channel = f'/ch/{self.source_channel:02}'
            self._change(f'{channel}/config/rtnsrc', self.source_channel - 1)
            self._change(f'{channel}/preamp/rtnsw', 1)
            self._change(f'{channel}/preamp/rtntrim', 0.5)
            self._change(f'{channel}/preamp/hpon', 0)
            self._change(f'{channel}/gate/on', 0)
            self._change(f'{channel}/dyn/on', 0)
            self._change(f'{channel}/insert/on', 0)
            self._change(f'{channel}/eq/on', 0)
            self._change(f'{channel}/grp/dca', 0)
            self._change(f'{channel}/grp/mute', 0)
            self._change(f'{channel}/mix/on', 1)
            self._change(f'{channel}/mix/lr', 0)
            self._change(f'{channel}/mix/fader', UNITY_FADER)
            for input_channel in self.channels:
                if input_channel <= 16:
                    headamp = f'/headamp/{input_channel:02}'
                    self._change(f'{headamp}/phantom', 0)
                    self._change(f'{headamp}/gain', 1 / 6)
            for send in self.sends:
                self._change(f'{channel}/mix/{send:02}/level', 0.0)
                bus = f'/bus/{send}'
                self._change(f'{bus}/dyn/on', 0)
                self._change(f'{bus}/insert/on', 0)
                self._change(f'{bus}/eq/on', 0)
                self._change(f'{bus}/grp/dca', 0)
                self._change(f'{bus}/grp/mute', 0)
                self._change(f'{bus}/mix/on', 1)
                self._change(f'{bus}/mix/fader', UNITY_FADER)
        except (OSError, TimeoutError, ValueError):
            self._restore()
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self._restore()

    def enable_sends(self) -> None:
        channel = f'/ch/{self.source_channel:02}/mix'
        for number in self.sends:
            self.osc.set(f'{channel}/{number:02}/level', UNITY_FADER)

    def _change(self, path: str, value: str | int | float | bool) -> None:
        self.saved.append((path, self.osc.query(path)))
        self.osc.set(path, value)

    def _restore(self) -> None:
        error: OSError | TimeoutError | ValueError | None = None
        for path, value in reversed(self.saved):
            try:
                self.osc.set(path, value)
            except (OSError, TimeoutError, ValueError) as e:
                error = error or e
        self.saved.clear()
        if error is not None:
            raise error


class CableTester:
    def __init__(
        self,
        recs: RecsClient,
        mixer: MixerSpec,
        *,
        osc_factory: Callable[
            [str, int], AbstractContextManager[OscControl]
        ] = X18OscClient,
        query_devices: Callable[[], Sequence[DeviceDict]] | None = None,
        round_trip: Callable[[str, int, int, np.ndarray], np.ndarray] | None = None,
    ) -> None:
        self.recs = recs
        self.mixer = mixer
        self.osc_factory = osc_factory
        self.query_devices = query_devices or audio_devices
        self.round_trip = round_trip or audio_round_trip

    def run(
        self,
        channels: list[int],
        sends: list[int],
        progress: Callable[[CableChannelResult], None] | None = None,
        duration_seconds: float = TONE_SECONDS,
    ) -> CableTestReport:
        validate_channels(channels)
        validate_sends(sends)
        validate_duration(duration_seconds)
        if self.mixer.osc is None:
            raise ValueError('X18 OSC control is not configured')
        resume = self.recs.pause_recording()
        if isinstance(resume, models.ActionResult):
            require_action(resume, 'pause recording')
        try:
            source_channel = next(i for i in range(1, 17) if i not in channels)
            device, sample_rate = find_audio_device(
                self.query_devices(),
                self.mixer.audio_device_names,
                source_channel,
            )
            results = self._test_channels(
                channels,
                sends,
                source_channel,
                device,
                sample_rate,
                progress,
                duration_seconds,
            )
        finally:
            if resume:
                require_action(self.recs.action('resume_recording'), 'resume recording')
        return CableTestReport(results=results)

    def _test_channels(
        self,
        channels: list[int],
        sends: list[int],
        source_channel: int,
        device: str,
        sample_rate: int,
        progress: Callable[[CableChannelResult], None] | None,
        duration_seconds: float,
    ) -> list[CableChannelResult]:
        if self.mixer.osc is None:
            raise ValueError('X18 OSC control is not configured')
        tone = sine_wave(sample_rate, duration_seconds)
        results = []
        with self.osc_factory(self.mixer.osc.host, self.mixer.osc.port) as osc:
            with X18TestRouting(osc, source_channel, channels, sends) as routing:
                routing.enable_sends()
                recorded = self.round_trip(device, source_channel, sample_rate, tone)
                for channel in channels:
                    result = analyze(
                        channel, tone, recorded[:, channel - 1], sample_rate
                    )
                    results.append(result)
                    if not result.passed and progress is not None:
                        progress(result)
        return results


def main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine('showco cable-test')
    options = tyro.cli(
        CableTestOptions,
        args=argv,
        description='Test X18 analog cables between AUX sends and inputs',
    )
    try:
        channels = parse_range(options.channels, 1, 18, 'channels')
        sends = parse_range(options.sends, 1, 6, 'sends')
        tester = cable_tester_from_specs(
            RecsClient(), load_mixer_specs(options.mixers_config)
        )
        report = tester.run(
            channels,
            sends,
            lambda r: print(r.line(), flush=True),
            options.duration_seconds,
        )
    except (ConnectionError, OSError, TimeoutError, ValueError) as e:
        print(f'ERROR: {e}')
        return 1
    passed = sum(r.passed for r in report.results)
    print(f'Cable test complete: {passed}/{len(report.results)} passed')
    return 0 if report.passed else 1


def cable_tester_from_specs(recs: RecsClient, specs: list[MixerSpec]) -> CableTester:
    mixer = next((m for m in specs if m.name == 'X18'), None)
    if mixer is None:
        raise ValueError('X18 mixer is not configured')
    return CableTester(recs, mixer)


def parse_range(value: str, minimum: int, maximum: int, name: str) -> list[int]:
    parts = value.split('-')
    if len(parts) not in {1, 2}:
        raise ValueError(f'{name} must be N or N-M')
    try:
        first = int(parts[0])
        last = int(parts[-1])
    except ValueError:
        raise ValueError(f'{name} must be N or N-M') from None
    if first > last:
        raise ValueError(f'{name} range must be ascending')
    if first < minimum or last > maximum:
        raise ValueError(f'{name} must be within {minimum}-{maximum}')
    return list(range(first, last + 1))


def validate_channels(channels: list[int]) -> None:
    if not channels:
        raise ValueError('channels must not be empty')
    if min(channels) < 1 or max(channels) > 18:
        raise ValueError('channels must be within 1-18')
    if all(i in channels for i in range(1, 17)):
        raise ValueError(
            'leave at least one input channel in 1-16 unused for the test source'
        )


def validate_sends(sends: list[int]) -> None:
    if not sends:
        raise ValueError('sends must not be empty')
    if min(sends) < 1 or max(sends) > 6:
        raise ValueError('sends must be within 1-6')


def validate_duration(seconds: float) -> None:
    if not isfinite(seconds) or seconds <= ANALYSIS_MARGIN_SECONDS * 2:
        raise ValueError(
            f'duration must be greater than {ANALYSIS_MARGIN_SECONDS * 2:g} seconds'
        )


def audio_devices() -> Sequence[DeviceDict]:
    return cast(Sequence[DeviceDict], sounddevice.query_devices())


def find_audio_device(
    devices: Sequence[DeviceDict], names: list[str], output_channels: int
) -> tuple[str, int]:
    matches = []
    for device in devices:
        name = str(device.get('name', ''))
        outputs = int(device.get('max_output_channels', 0))
        if not any(name.startswith(prefix) for prefix in names):
            continue
        if outputs >= output_channels:
            if match := re.search(r'\((hw:\d+,\d+)\)$', name):
                sample_rate = int(float(device.get('default_samplerate', 48_000)))
                return match.group(1), sample_rate
            matches.append(f'{name} (no ALSA hardware address)')
        else:
            matches.append(f'{name} ({outputs} output)')
    if matches:
        raise ValueError(
            f'X18 USB audio device needs at least {output_channels} output '
            f'channels; found {", ".join(matches)}'
        )
    raise ValueError('X18 USB audio device not found')


def sine_wave(sample_rate: int, duration_seconds: float = TONE_SECONDS) -> np.ndarray:
    validate_duration(duration_seconds)
    frames = round(duration_seconds * sample_rate)
    times = np.arange(frames, dtype=np.float32) / sample_rate
    tone = (TONE_AMPLITUDE * np.sin(2 * pi * TONE_FREQUENCY * times)).astype(np.float32)
    fade_frames = min(round(0.05 * sample_rate), frames // 2)
    fade = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
    tone[:fade_frames] *= fade
    tone[-fade_frames:] *= fade[::-1]
    return tone


def audio_round_trip(
    device: str,
    source_channel: int,
    sample_rate: int,
    tone: np.ndarray,
) -> np.ndarray:
    output = np.zeros((tone.size, X18_CHANNELS), dtype=np.int32)
    output[:, source_channel - 1] = np.rint(tone * ((1 << 23) - 1)).astype(np.int32)
    with TemporaryDirectory() as directory:
        path = Path(directory)
        output_path = path / 'output.raw'
        recorded_path = path / 'recorded.raw'
        output_path.write_bytes(encode_s24le(output))
        recorder = subprocess.Popen(
            [
                'arecord',
                '-D',
                device,
                '-t',
                'raw',
                '-f',
                X18_SAMPLE_FORMAT,
                '-c',
                str(X18_CHANNELS),
                '-r',
                str(sample_rate),
                '-d',
                str(round(tone.size / sample_rate)),
                str(recorded_path),
            ],
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            playback = subprocess.run(
                [
                    'aplay',
                    '-D',
                    device,
                    '-t',
                    'raw',
                    '-f',
                    X18_SAMPLE_FORMAT,
                    '-c',
                    str(X18_CHANNELS),
                    '-r',
                    str(sample_rate),
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=tone.size / sample_rate + 5,
            )
            _, recording_error = recorder.communicate(
                timeout=tone.size / sample_rate + 5
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError('X18 audio playback or recording timed out') from error
        finally:
            original_error = sys.exception()
            try:
                if recorder.poll() is None:
                    recorder.kill()
                    recorder.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                if original_error is None:
                    raise
                print(f'X18 recorder cleanup failed: {error}', file=sys.stderr)
        if playback.returncode:
            raise ValueError(f'X18 playback failed: {playback.stderr.strip()}')
        if recorder.returncode:
            raise ValueError(f'X18 recording failed: {recording_error.strip()}')
        recorded = decode_s24le(recorded_path.read_bytes())
    if recorded.shape != output.shape:
        raise ValueError('X18 recorded an unexpected number of frames')
    return recorded


def encode_s24le(samples: np.ndarray) -> bytes:
    values = samples.reshape(-1)
    encoded = np.empty(values.size * 3, dtype=np.uint8)
    encoded[0::3] = values & 0xFF
    encoded[1::3] = values >> 8 & 0xFF
    encoded[2::3] = values >> 16 & 0xFF
    return encoded.tobytes()


def decode_s24le(data: bytes) -> np.ndarray:
    encoded = np.frombuffer(data, dtype=np.uint8)
    if encoded.size % (X18_CHANNELS * 3):
        raise ValueError('X18 recorded an invalid sample size')
    values = (
        encoded[0::3].astype(np.int32)
        | encoded[1::3].astype(np.int32) << 8
        | encoded[2::3].astype(np.int32) << 16
    )
    values[values >= 1 << 23] -= 1 << 24
    return values.reshape(-1, X18_CHANNELS).astype(np.float32) / (1 << 23)


def analyze(
    channel: int,
    sent: np.ndarray,
    recorded: np.ndarray,
    sample_rate: int,
) -> CableChannelResult:
    if recorded.shape != sent.shape:
        raise ValueError('X18 recorded an unexpected number of frames')
    margin = round(ANALYSIS_MARGIN_SECONDS * sample_rate)
    signal = recorded[margin:-margin].astype(np.float64)
    peak = float(np.max(np.abs(signal)))
    signal -= np.mean(signal)
    positions = np.arange(signal.size, dtype=np.float64) / sample_rate
    sine = np.sin(2 * pi * TONE_FREQUENCY * positions)
    cosine = np.cos(2 * pi * TONE_FREQUENCY * positions)
    sine_amplitude = 2 * float(np.dot(signal, sine)) / signal.size
    cosine_amplitude = 2 * float(np.dot(signal, cosine)) / signal.size
    tone_rms = float(np.hypot(sine_amplitude, cosine_amplitude) / np.sqrt(2))
    rms = float(np.sqrt(np.mean(np.square(signal))))
    sent_rms = float(np.sqrt(np.mean(np.square(sent[margin:-margin]))))
    similarity = min(1.0, tone_rms / rms) if rms else 0.0
    level_ratio = tone_rms / sent_rms if sent_rms else 0.0
    passed = (
        similarity >= MINIMUM_SIMILARITY
        and MINIMUM_LEVEL_RATIO <= level_ratio <= MAXIMUM_LEVEL_RATIO
        and peak < 0.99
    )
    return CableChannelResult(
        channel=channel,
        passed=passed,
        similarity=similarity,
        level_ratio=level_ratio,
        rms=rms,
        peak=peak,
    )


def require_action(result: models.ActionResult, description: str) -> None:
    if not result.ok:
        raise ConnectionError(f'could not {description}: {result.message}')
