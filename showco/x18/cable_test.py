from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from math import pi
from pathlib import Path
from time import monotonic, sleep
from typing import Annotated, Protocol, cast

import numpy as np
import sounddevice
import tyro
from pydantic import BaseModel
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
TONE_AMPLITUDE = 0.1
MINIMUM_SIMILARITY = 0.98
MINIMUM_LEVEL_RATIO = 0.7
MAXIMUM_LEVEL_RATIO = 1.3
OSC_TIMEOUT_SECONDS = 1.0
AUDIO_RELEASE_TIMEOUT_SECONDS = 2.0
UNITY_FADER = 0.75


class CableTestOptions(BaseModel, frozen=True):
    channels: Annotated[str, tyro.conf.Positional] = DEFAULT_CHANNELS
    sends: Annotated[str, tyro.conf.Positional] = DEFAULT_SENDS
    mixers_config: Path = Path.home() / '.config/showco/mixers.toml'


class CablePairResult(BaseModel, frozen=True):
    send: int
    channel: int
    passed: bool
    similarity: float
    level_ratio: float
    rms: float
    peak: float

    def line(self) -> str:
        state = 'PASS' if self.passed else 'FAIL'
        return (
            f'{state}: send {self.send} -> channel {self.channel}: '
            f'{self.similarity:.1%} tone match, {self.level_ratio:.1%} level'
        )


class CableTestReport(BaseModel, frozen=True):
    results: list[CablePairResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def message(self) -> str:
        passed = sum(r.passed for r in self.results)
        return '\n'.join(
            [f'Cable test: {passed}/{len(self.results)} passed']
            + [r.line() for r in self.results]
        )


class OscControl(Protocol):
    def query(self, path: str) -> str | int | float | bool: ...

    def set(self, path: str, value: str | int | float | bool) -> None: ...


class X18OscClient:
    def __init__(
        self, host: str, port: int, timeout_seconds: float = OSC_TIMEOUT_SECONDS
    ) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
                f'X18 did not reply to {path} within {OSC_TIMEOUT_SECONDS}s'
            ) from None

    def set(self, path: str, value: str | int | float | bool) -> None:
        self.socket.send(encode_message(path, [value]))

    def _request(
        self, path: str, arguments: list[str | int | float | bool]
    ) -> str | int | float | bool:
        self.socket.send(encode_message(path, arguments))
        while True:
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
            self.osc.set('/lr/mix/on', 0)
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

    def select_send(self, send: int) -> None:
        channel = f'/ch/{self.source_channel:02}/mix'
        for number in self.sends:
            level = UNITY_FADER if number == send else 0.0
            self.osc.set(f'{channel}/{number:02}/level', level)

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
        try:
            self.osc.set('/lr/mix/on', 1)
        except (OSError, TimeoutError, ValueError) as e:
            error = error or e
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
        round_trip: Callable[[int, int, int, int, np.ndarray], np.ndarray]
        | None = None,
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
        progress: Callable[[CablePairResult], None] | None = None,
    ) -> CableTestReport:
        validate_pairs(channels, sends)
        if self.mixer.osc is None:
            raise ValueError('X18 OSC control is not configured')
        resume = self.recs.pause_recording()
        if isinstance(resume, models.ActionResult):
            require_action(resume, 'pause recording')
        try:
            source_channel = next(i for i in range(1, 17) if i not in channels)
            device, sample_rate = wait_for_audio_device(
                self.query_devices,
                self.mixer.audio_device_names,
                max(channels),
                source_channel,
            )
            results = self._test_pairs(
                channels,
                sends,
                source_channel,
                device,
                sample_rate,
                progress,
            )
        finally:
            if resume:
                require_action(self.recs.action('resume_recording'), 'resume recording')
        return CableTestReport(results=results)

    def _test_pairs(
        self,
        channels: list[int],
        sends: list[int],
        source_channel: int,
        device: int,
        sample_rate: int,
        progress: Callable[[CablePairResult], None] | None,
    ) -> list[CablePairResult]:
        if self.mixer.osc is None:
            raise ValueError('X18 OSC control is not configured')
        tone = sine_wave(sample_rate)
        results = []
        with self.osc_factory(self.mixer.osc.host, self.mixer.osc.port) as osc:
            with X18TestRouting(osc, source_channel, channels, sends) as routing:
                for channel, send in zip(channels, sends, strict=True):
                    routing.select_send(send)
                    result = analyze(
                        send,
                        channel,
                        tone,
                        self.round_trip(
                            device, source_channel, channel, sample_rate, tone
                        ),
                        sample_rate,
                    )
                    results.append(result)
                    if progress is not None:
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
        report = tester.run(channels, sends, lambda r: print(r.line(), flush=True))
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


def validate_pairs(channels: list[int], sends: list[int]) -> None:
    if len(channels) != len(sends):
        raise ValueError('channel and send ranges must have the same length')
    if not channels:
        raise ValueError('channel and send ranges must not be empty')
    if min(channels) < 1 or max(channels) > 18:
        raise ValueError('channels must be within 1-18')
    if min(sends) < 1 or max(sends) > 6:
        raise ValueError('sends must be within 1-6')


def audio_devices() -> Sequence[DeviceDict]:
    return cast(Sequence[DeviceDict], sounddevice.query_devices())


def find_audio_device(
    devices: Sequence[DeviceDict],
    names: list[str],
    input_channels: int,
    output_channels: int,
) -> tuple[int, int]:
    matches = []
    for index, device in enumerate(devices):
        name = str(device.get('name', ''))
        inputs = int(device.get('max_input_channels', 0))
        outputs = int(device.get('max_output_channels', 0))
        if not any(name.startswith(prefix) for prefix in names):
            continue
        if inputs >= input_channels and outputs >= output_channels:
            return index, int(float(device.get('default_samplerate', 48_000)))
        matches.append(f'{name} ({inputs} input, {outputs} output)')
    if matches:
        raise ValueError(
            f'X18 USB audio device needs at least {input_channels} input and '
            f'{output_channels} output channels; found {", ".join(matches)}'
        )
    raise ValueError('X18 USB audio device not found')


def wait_for_audio_device(
    query_devices: Callable[[], Sequence[DeviceDict]],
    names: list[str],
    input_channels: int,
    output_channels: int,
) -> tuple[int, int]:
    deadline: float | None = None
    while True:
        try:
            return find_audio_device(
                query_devices(), names, input_channels, output_channels
            )
        except ValueError:
            now = monotonic()
            if deadline is None:
                deadline = now + AUDIO_RELEASE_TIMEOUT_SECONDS
            elif now >= deadline:
                raise
            sleep(0.1)


def sine_wave(sample_rate: int) -> np.ndarray:
    frames = round(TONE_SECONDS * sample_rate)
    times = np.arange(frames, dtype=np.float32) / sample_rate
    tone = (TONE_AMPLITUDE * np.sin(2 * pi * TONE_FREQUENCY * times)).astype(np.float32)
    fade_frames = min(round(0.05 * sample_rate), frames // 2)
    fade = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
    tone[:fade_frames] *= fade
    tone[-fade_frames:] *= fade[::-1]
    return tone


def audio_round_trip(
    device: int,
    source_channel: int,
    input_channel: int,
    sample_rate: int,
    tone: np.ndarray,
) -> np.ndarray:
    recorded = sounddevice.playrec(
        tone[:, np.newaxis],
        samplerate=sample_rate,
        channels=1,
        dtype='float32',
        device=(device, device),
        input_mapping=[input_channel],
        output_mapping=[source_channel],
        blocking=True,
    )
    return cast(np.ndarray, recorded[:, 0])


def analyze(
    send: int,
    channel: int,
    sent: np.ndarray,
    recorded: np.ndarray,
    sample_rate: int,
) -> CablePairResult:
    if recorded.shape != sent.shape:
        raise ValueError('X18 recorded an unexpected number of frames')
    margin = round(0.25 * sample_rate)
    signal = recorded[margin:-margin].astype(np.float64)
    signal -= np.mean(signal)
    positions = np.arange(signal.size, dtype=np.float64) / sample_rate
    sine = np.sin(2 * pi * TONE_FREQUENCY * positions)
    cosine = np.cos(2 * pi * TONE_FREQUENCY * positions)
    sine_amplitude = 2 * float(np.dot(signal, sine)) / signal.size
    cosine_amplitude = 2 * float(np.dot(signal, cosine)) / signal.size
    tone_rms = float(np.hypot(sine_amplitude, cosine_amplitude) / np.sqrt(2))
    rms = float(np.sqrt(np.mean(np.square(signal))))
    peak = float(np.max(np.abs(signal)))
    sent_rms = float(np.sqrt(np.mean(np.square(sent[margin:-margin]))))
    similarity = min(1.0, tone_rms / rms) if rms else 0.0
    level_ratio = tone_rms / sent_rms if sent_rms else 0.0
    passed = (
        similarity >= MINIMUM_SIMILARITY
        and MINIMUM_LEVEL_RATIO <= level_ratio <= MAXIMUM_LEVEL_RATIO
        and peak < 0.99
    )
    return CablePairResult(
        send=send,
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
