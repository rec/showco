"""Monitor Linux ALSA capture and optionally guard a private streamO test."""

import os
import sys
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from select import select
from subprocess import PIPE, STDOUT, Popen, check_output, run
from time import monotonic, sleep
from typing import Literal, TextIO

import tyro
from pydantic import BaseModel


class Options(BaseModel, frozen=True):
    substream: Path
    guard_loopback: bool = False


class CaptureGuard:
    def __init__(self) -> None:
        self.crossings: deque[float] = deque(maxlen=3)
        self.progress: deque[tuple[float, int]] = deque()
        self.high_since: float | None = None
        self.throttled = False
        self.speed: float | None = None

    def record_progress(self, now: float, output_us: int) -> None:
        if self.progress and (
            output_us < self.progress[-1][1] or now - self.progress[-1][0] > 1
        ):
            self.progress.clear()
        self.progress.append((now, output_us))
        while now - self.progress[0][0] > 2.5:
            self.progress.popleft()
        started, first_output = self.progress[0]
        elapsed = now - started
        self.speed = (
            (output_us - first_output) / (elapsed * 1_000_000)
            if elapsed >= 1.5
            else None
        )

    def sample(self, now: float, fill: float) -> Literal['throttle', 'stop'] | None:
        if fill < 0.5:
            self.high_since = None
        elif self.high_since is None:
            self.high_since = now
            self.crossings.append(now)
        repeated = len(self.crossings) == 3 and now - self.crossings[0] <= 1.5
        if self.throttled:
            if (
                repeated
                or fill >= 0.75
                or (self.high_since is not None and now - self.high_since >= 0.05)
            ):
                return 'stop'
        elif (
            repeated
            and self.speed is not None
            and self.speed < 0.85
            and now - self.progress[-1][0] <= 1
        ):
            return 'throttle'
        return None

    def mark_throttled(self) -> None:
        self.throttled = True
        self.crossings.clear()
        self.high_since = None


class Loopback:
    def __init__(self) -> None:
        self.unit = f'showco-streamo-guarded-{os.getpid()}.service'
        self.guard = CaptureGuard()
        self.output = b''
        self.deadline = monotonic() + 300
        self.control = ThreadPoolExecutor(max_workers=1)
        self.throttle_request: Future[None] | None = None
        self.process = Popen(
            [
                'systemd-run',
                '--user',
                '--quiet',
                '--pipe',
                '--wait',
                '--collect',
                f'--unit={self.unit}',
                '--property=RuntimeMaxSec=310s',
                f'--working-directory={Path.home()}',
                '--property=TimeoutStopSec=1s',
                sys.executable,
                str(Path(__file__).with_name('pi-streamo-loopback.py').resolve()),
            ],
            stdout=PIPE,
            stderr=STDOUT,
        )

    def update_progress(self) -> None:
        pipe = self.process.stdout
        assert pipe is not None
        if select([pipe], [], [], 0)[0]:
            lines = (self.output + os.read(pipe.fileno(), 8192)).split(b'\n')
            self.output = lines.pop()
            for line in lines:
                if line.startswith(
                    prefix := b'INFO:streamo.streamer:FFmpeg: out_time_us='
                ):
                    value = line.removeprefix(prefix)
                    if value.isdigit():
                        self.guard.record_progress(monotonic(), int(value))
                elif not line.startswith(b'INFO:streamo.streamer:FFmpeg: '):
                    print(line.decode(errors='replace'), file=sys.stderr, flush=True)
        if (result := self.process.poll()) is not None:
            raise RuntimeError(f'Private loopback exited unexpectedly ({result})')
        if self.throttle_request is not None and self.throttle_request.done():
            self.throttle_request.result()
            self.throttle_request = None
            self.guard.mark_throttled()
            print(f'{datetime.now(UTC).isoformat()} throttle applied', flush=True)

    def throttle(self) -> None:
        run(
            [
                'systemctl',
                '--user',
                'set-property',
                '--runtime',
                self.unit,
                'CPUQuota=75%',
                'CPUQuotaPeriodSec=10ms',
            ],
            check=True,
            timeout=1,
        )
        group = check_output(
            ['systemctl', '--user', 'show', '-p', 'ControlGroup', '--value', self.unit],
            text=True,
            timeout=1,
        ).strip()
        quota = (
            (Path('/sys/fs/cgroup') / group.lstrip('/') / 'cpu.max').read_text().split()
        )
        if quota != ['7500', '10000']:
            raise RuntimeError(f'Unexpected private loopback CPU quota: {quota}')

    def stop(self) -> None:
        try:
            if self.process.poll() is None:
                run(['systemctl', '--user', 'stop', self.unit], check=True, timeout=5)
            self.process.wait(timeout=5)
        finally:
            self.control.shutdown()
            if self.process.stdout is not None:
                self.process.stdout.close()


def monitor(substream: Path, *, guard_loopback: bool = False) -> None:
    params = dict(
        line.split(': ', 1)
        for line in (substream / 'hw_params').read_text().splitlines()
    )
    capacity = int(params['buffer_size'])
    rate = int(params['rate'].split()[0])
    period = int(params['period_size']) / rate
    print(
        f'buffer_frames={capacity} rate={rate} sample_interval_ms={period * 500:.1f}',
        flush=True,
    )
    with (substream / 'status').open() as status:
        loopback = Loopback() if guard_loopback else None
        try:
            sample_capture(status, capacity, period, loopback)
        finally:
            if loopback is not None:
                loopback.stop()


def sample_capture(
    status: TextIO, capacity: int, period: float, loopback: Loopback | None
) -> None:
    previous_level = ''
    previous_sample = monotonic()
    next_report = previous_sample + 10
    peak = 0
    max_sample_gap = 0.0
    while True:
        status.seek(0)
        values = {
            key.strip(): value for key, _, value in (s.partition(':') for s in status)
        }
        sampled_at = monotonic()
        max_sample_gap = max(max_sample_gap, sampled_at - previous_sample)
        previous_sample = sampled_at
        state = values.get('state', 'closed').strip()
        pending = None
        if state == 'RUNNING':
            pending = max(int(values['avail']), int(values['avail_max']))
            peak = max(peak, pending)
            level = 'warning' if pending * 2 >= capacity else 'normal'
            if pending * 4 >= capacity * 3:
                level = 'critical'
        else:
            level = state
        if level != previous_level or sampled_at >= next_report:
            timestamp = datetime.now(UTC).isoformat(timespec='milliseconds')
            if level != previous_level:
                print(f'{timestamp} {level} pending_frames={pending}', flush=True)
            if sampled_at >= next_report:
                speed = loopback.guard.speed if loopback is not None else None
                encoder = f' encoder_speed={speed:.2f}x' if speed is not None else ''
                print(
                    f'{timestamp} peak_frames={peak} '
                    f'max_sample_gap_ms={max_sample_gap * 1000:.1f}{encoder}',
                    flush=True,
                )
                next_report = sampled_at + 10
                peak = 0
                max_sample_gap = 0.0
        previous_level = level
        if loopback is not None:
            loopback.update_progress()
            if sampled_at >= loopback.deadline:
                print('Private loopback test window complete', flush=True)
                return
            if pending is None:
                print(f'Stopping private loopback: capture state {state}', flush=True)
                return
            action = loopback.guard.sample(monotonic(), pending / capacity)
            if action == 'throttle' and loopback.throttle_request is None:
                speed = loopback.guard.speed
                assert speed is not None
                print(
                    f'{datetime.now(UTC).isoformat()} throttling private loopback: '
                    f'encoder {speed:.2f}x, repeated capture pressure',
                    flush=True,
                )
                loopback.throttle_request = loopback.control.submit(loopback.throttle)
            elif action == 'stop':
                print(
                    f'{datetime.now(UTC).isoformat()} stopping private loopback: '
                    'capture pressure persists after throttle',
                    flush=True,
                )
                return
        sleep(period / 2)


if __name__ == '__main__':
    options = tyro.cli(Options)
    monitor(options.substream, guard_loopback=options.guard_loopback)
