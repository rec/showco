from __future__ import annotations

import random
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

import numpy as np
import sounddevice
from pydantic import BaseModel
from reccy.device import DeviceDict

from .cable_test import X18_CHANNELS, OscControl, X18OscClient, find_audio_device

SAMPLE_RATE = 48_000
BLOCK_FRAMES = 1_024
MUSIC_FADER = 0.4


class MusicPlayerStatus(BaseModel, frozen=True):
    state: str = 'stopped'
    directory: Path | None = None
    track: Path | None = None
    error: str | None = None


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
            osc.set(
                f'/config/chlink/{self.source_channels[0]}-{self.source_channels[1]}', 0
            )
            for channel in self.source_channels:
                prefix = f'/ch/{channel:02}'
                osc.set(f'{prefix}/config/rtnsrc', channel - 1)
                osc.set(f'{prefix}/preamp/rtnsw', 1)
                osc.set(f'{prefix}/mix/on', 1)
                osc.set(f'{prefix}/mix/lr', 1)
                osc.set(f'{prefix}/mix/fader', MUSIC_FADER)

    def disable(self) -> None:
        with self.osc_factory(self.host, self.port) as osc:
            for channel in self.source_channels:
                osc.set(f'/ch/{channel:02}/preamp/rtnsw', 0)
                osc.set(f'/ch/{channel:02}/mix/lr', 0)
                osc.set(f'/ch/{channel:02}/mix/on', 0)


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

    def start(self, directory: Path, shuffle: bool, fade_seconds: float) -> None:
        tracks = music_files(directory)
        if not tracks:
            raise ValueError(f'No audio files in {directory}')
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
            self.status_value = MusicPlayerStatus(state='playing', directory=directory)
            self.thread = threading.Thread(
                target=self._play, args=(tracks, shuffle), name='showco music'
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

    def _play(self, tracks: list[Path], shuffle: bool) -> None:
        previous: Path | None = None
        while not self.stop_requested.is_set():
            playlist = ordered_tracks(tracks, shuffle, previous)
            for track in playlist:
                if self.stop_requested.is_set():
                    return
                try:
                    self._play_track(track)
                except (
                    OSError,
                    sounddevice.PortAudioError,
                    subprocess.TimeoutExpired,
                ) as error:
                    with self.lock:
                        self.status_value = self.status_value.model_copy(
                            update={'state': 'failed', 'error': str(error)}
                        )
                    return
                previous = track

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
            stderr=subprocess.PIPE,
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
            process.wait(timeout=5)
            with self.lock:
                if self.process is process:
                    self.process = None


def music_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f'Music directory does not exist: {directory}')
    return sorted(path for path in directory.iterdir() if path.is_file())


def ordered_tracks(
    tracks: list[Path], shuffle: bool, previous: Path | None
) -> list[Path]:
    result = list(tracks)
    if shuffle:
        random.SystemRandom().shuffle(result)
        if len(result) > 1 and result[0] == previous:
            result[0], result[1] = result[1], result[0]
    return result
