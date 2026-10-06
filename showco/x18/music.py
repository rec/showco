from __future__ import annotations

import random
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from math import isclose, isfinite
from pathlib import Path

import numpy as np
import sounddevice
from pydantic import BaseModel, Field, field_validator
from reccy.device import DeviceDict

from .cable_test import X18_CHANNELS, OscControl, X18OscClient, find_audio_device

SAMPLE_RATE = 48_000
BLOCK_FRAMES = 1_024
MUSIC_FADER = 0.4


class Files(BaseModel, frozen=True):
    files: list[Path]
    shuffle: bool | list[int | float] = False

    @field_validator('shuffle')
    @classmethod
    def validate_shuffle(
        cls, value: bool | list[int | float]
    ) -> bool | list[int | float]:
        if isinstance(value, list) and any(not isfinite(v) or v < 0 for v in value):
            raise ValueError('shuffle weights must be finite and nonnegative')
        return value


class MusicPlayerStatus(BaseModel, frozen=True):
    state: str = 'stopped'
    directory: Path | None = None
    track: Path | None = None
    error: str | None = None


class RoomScene(BaseModel, frozen=True):
    master_fader: float = Field(ge=0, le=1, allow_inf_nan=False)
    input_lr: list[int] = Field(min_length=X18_CHANNELS, max_length=X18_CHANNELS)

    @field_validator('input_lr')
    @classmethod
    def validate_input_lr(cls, value: list[int]) -> list[int]:
        if any(v not in (0, 1) for v in value):
            raise ValueError('room LR routing must contain only 0 or 1')
        return value


class X18MusicRouting:
    def __init__(
        self,
        host: str,
        port: int,
        source_channels: list[int],
        *,
        osc_factory: Callable[[str, int], AbstractContextManager[OscControl]] = (
            X18OscClient
        ),
    ) -> None:
        self.host = host
        self.port = port
        self.source_channels = source_channels
        self.osc_factory = osc_factory

    def enable(self) -> None:
        with self.osc_factory(self.host, self.port) as osc:
            self._set_confirmed(
                osc,
                f'/config/chlink/{self.source_channels[0]}-{self.source_channels[1]}',
                0,
            )
            for channel in self.source_channels:
                prefix = f'/ch/{channel:02}'
                self._set_confirmed(osc, f'{prefix}/config/rtnsrc', channel - 1)
                self._set_confirmed(osc, f'{prefix}/preamp/rtnsw', 1)
                self._set_confirmed(osc, f'{prefix}/mix/on', 1)
                self._set_confirmed(osc, f'{prefix}/mix/lr', 1)
                self._set_confirmed(osc, f'{prefix}/mix/fader', MUSIC_FADER)

    def disable(self) -> None:
        errors: list[str] = []
        with self.osc_factory(self.host, self.port) as osc:
            for channel in self.source_channels:
                for path in (
                    f'/ch/{channel:02}/preamp/rtnsw',
                    f'/ch/{channel:02}/mix/lr',
                    f'/ch/{channel:02}/mix/on',
                ):
                    try:
                        self._set_confirmed(osc, path, 0)
                    except (OSError, TimeoutError, ValueError) as error:
                        errors.append(f'{path}: {error}')
        if errors:
            raise ValueError(
                'Music returns could not be confirmed muted: ' + '; '.join(errors)
            )

    def capture_room(self) -> RoomScene:
        with self.osc_factory(self.host, self.port) as osc:
            master = osc.query('/lr/mix/fader')
            if not isinstance(master, int | float) or not 0 <= master <= 1:
                raise ValueError(f'Invalid X18 main LR fader: {master}')
            routes: list[int] = []
            for channel in range(1, X18_CHANNELS + 1):
                value = osc.query(f'/ch/{channel:02}/mix/lr')
                if value not in (0, 1):
                    raise ValueError(f'Invalid X18 channel {channel} LR route: {value}')
                routes.append(int(value))
        return RoomScene(master_fader=float(master), input_lr=routes)

    def fade_main(self, target: float, seconds: float) -> None:
        with self.osc_factory(self.host, self.port) as osc:
            start = osc.query('/lr/mix/fader')
            if not isinstance(start, int | float) or not 0 <= start <= 1:
                raise ValueError(f'Invalid X18 main LR fader: {start}')
            steps = max(1, round(seconds * 20))
            for step in range(1, steps + 1):
                osc.set(
                    '/lr/mix/fader',
                    float(start) + (target - float(start)) * step / steps,
                )
                if step < steps:
                    time.sleep(seconds / steps)
            observed = osc.query('/lr/mix/fader')
            if not isinstance(observed, int | float) or not isclose(
                observed, target, rel_tol=0, abs_tol=1e-5
            ):
                raise ValueError(f'X18 main LR fader did not reach {target}')

    def isolate_instruments(self, scene: RoomScene) -> None:
        with self.osc_factory(self.host, self.port) as osc:
            for channel, enabled in enumerate(scene.input_lr, 1):
                if channel not in self.source_channels and enabled:
                    self._set_confirmed(osc, f'/ch/{channel:02}/mix/lr', 0)

    def restore_room(self, scene: RoomScene) -> None:
        with self.osc_factory(self.host, self.port) as osc:
            for channel, enabled in enumerate(scene.input_lr, 1):
                if channel not in self.source_channels:
                    self._set_confirmed(osc, f'/ch/{channel:02}/mix/lr', enabled)
            self._set_confirmed(osc, '/lr/mix/fader', scene.master_fader)

    @staticmethod
    def _set_confirmed(osc: OscControl, path: str, value: int | float) -> None:
        osc.set(path, value)
        observed = osc.query(path)
        if not isinstance(observed, int | float) or not isclose(
            observed, value, rel_tol=0, abs_tol=1e-5
        ):
            raise ValueError(f'X18 did not apply {path}={value}; observed {observed}')


class MusicPlayer:
    def __init__(
        self,
        source_channels: list[int],
        audio_device_names: list[str],
        *,
        query_devices: Callable[[], Sequence[DeviceDict]] | None = None,
        stream_factory: Callable[..., sounddevice.OutputStream] = (
            sounddevice.OutputStream
        ),
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.source_channels = source_channels
        self.audio_device_names = audio_device_names
        self.query_devices = query_devices or sounddevice.query_devices
        self.stream_factory = stream_factory
        self.process_factory = process_factory
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.stream: sounddevice.OutputStream | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.status_value = MusicPlayerStatus()
        self.level = 0.0

    def status(self) -> MusicPlayerStatus:
        with self.lock:
            return self.status_value

    def start(self, paths: list[Path], fade_seconds: float) -> None:
        playlists = audio_files(paths)
        if not any(s.files for s in playlists):
            raise ValueError('No audio files in the configured audio paths')
        device, _ = find_audio_device(
            self.query_devices(), self.audio_device_names, max(self.source_channels)
        )
        self.stop(0)
        stream = self.stream_factory(
            device=device,
            samplerate=SAMPLE_RATE,
            channels=X18_CHANNELS,
            dtype='float32',
            blocksize=BLOCK_FRAMES,
        )
        stream.start()
        with self.lock:
            self.stream = stream
            self.stop_requested.clear()
            self.level = 0.0
            self.status_value = MusicPlayerStatus(
                state='playing',
                directory=paths[0] if paths[0].is_dir() else paths[0].parent,
            )
            self.thread = threading.Thread(
                target=self._play,
                args=(playlists,),
                name='showco music',
                daemon=True,
            )
            self.thread.start()
        self.fade_to(1.0, fade_seconds)

    def stop(self, fade_seconds: float) -> None:
        if self.thread is None:
            return
        self.fade_to(0.0, fade_seconds)
        self.stop_requested.set()
        with self.lock:
            if self.process is not None:
                self.process.terminate()
            thread = self.thread
        thread.join(timeout=5)
        if thread.is_alive():
            with self.lock:
                self.status_value = self.status_value.model_copy(
                    update={
                        'state': 'failed',
                        'error': 'Music player did not stop within 5 seconds',
                    }
                )
            raise TimeoutError('Music player did not stop within 5 seconds')
        with self.lock:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
            self.stream = None
            self.thread = None
            self.process = None
            self.status_value = MusicPlayerStatus()

    def fade_to(self, target: float, seconds: float) -> None:
        with self.lock:
            start = self.level
        steps = max(1, round(seconds * 20))
        for step in range(1, steps + 1):
            with self.lock:
                self.level = start + (target - start) * step / steps
            if step < steps:
                time.sleep(seconds / steps)

    def _play(self, playlists: list[Files]) -> None:
        playable = [p for s in playlists for p in s.files]
        while not self.stop_requested.is_set():
            playlist = [p for s in playlists for p in ordered_tracks(s)]
            for track in playlist:
                if track not in playable:
                    continue
                if self.stop_requested.is_set():
                    return
                try:
                    self._play_track(track)
                except (
                    OSError,
                    sounddevice.PortAudioError,
                    subprocess.TimeoutExpired,
                ) as error:
                    playable = [p for p in playable if p != track]
                    with self.lock:
                        self.status_value = self.status_value.model_copy(
                            update={
                                'state': 'playing' if playable else 'failed',
                                'error': f'Skipped {track.name}: {error}',
                            }
                        )
                    if not playable:
                        return
                    continue

    def _play_track(self, track: Path) -> None:
        process = self.process_factory(
            [
                'ffmpeg',
                '-v',
                'error',
                '-i',
                str(track),
                '-f',
                'f32le',
                '-ac',
                '2',
                '-ar',
                str(SAMPLE_RATE),
                'pipe:1',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        with self.lock:
            self.process = process
            self.status_value = self.status_value.model_copy(update={'track': track})
        try:
            assert process.stdout is not None
            while not self.stop_requested.is_set():
                data = process.stdout.read(BLOCK_FRAMES * 2 * 4)
                if not data:
                    break
                samples = np.frombuffer(data, dtype=np.float32).reshape(-1, 2)
                output = np.zeros((samples.shape[0], X18_CHANNELS), dtype=np.float32)
                with self.lock:
                    level = self.level
                    stream = self.stream
                output[:, self.source_channels[0] - 1] = samples[:, 0] * level
                output[:, self.source_channels[1] - 1] = samples[:, 1] * level
                if stream is not None:
                    stream.write(output)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise
            with self.lock:
                if self.process is process:
                    self.process = None
        if returncode != 0 and not self.stop_requested.is_set():
            raise OSError(f'ffmpeg exited with status {returncode}')


def audio_files(paths: list[Path]) -> list[Files]:
    result: list[Files] = []
    for p in paths:
        if p.is_file():
            score = Files(files=[p])
        elif p.is_dir():
            score_path = p / 'score.toml'
            if score_path.is_file():
                score = Files.model_validate(tomllib.loads(score_path.read_text()))
                score = score.model_copy(update={'files': [p / f for f in score.files]})
            else:
                score = Files(
                    files=sorted(
                        f
                        for f in p.iterdir()
                        if f.is_file()
                        and f.suffix.lower()
                        in {
                            '.aac',
                            '.aif',
                            '.aiff',
                            '.flac',
                            '.m4a',
                            '.mp3',
                            '.ogg',
                            '.opus',
                            '.wav',
                        }
                    )
                )
        else:
            raise ValueError(f'Audio path does not exist: {p}')
        for f in score.files:
            if not f.is_file():
                raise ValueError(f'Audio file does not exist: {f}')
        result.append(score)
    return result


def ordered_tracks(score: Files) -> list[Path]:
    result = list(score.files)
    if not score.shuffle:
        return result
    generator = random.SystemRandom()
    if score.shuffle is True:
        generator.shuffle(result)
        return result
    weights = [score.shuffle[i % len(score.shuffle)] for i in range(len(result))]
    ordered: list[Path] = []
    while result:
        maximum = max(weights)
        index = generator.choices(
            range(len(result)),
            weights=[w / maximum for w in weights] if maximum else None,
        )[0]
        ordered.append(result.pop(index))
        weights.pop(index)
    return ordered
