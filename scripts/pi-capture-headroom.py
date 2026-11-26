"""Log capture-buffer pressure from an already running Linux ALSA substream."""

from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import tyro
from pydantic import BaseModel


class Options(BaseModel, frozen=True):
    substream: Path


def monitor(substream: Path) -> None:
    params = dict(
        line.split(': ', 1)
        for line in (substream / 'hw_params').read_text().splitlines()
    )
    capacity = int(params['buffer_size'])
    rate = int(params['rate'].split()[0])
    period = int(params['period_size']) / rate
    previous_level = ''
    previous_sample = monotonic()
    next_report = previous_sample + 10
    peak = 0
    max_sample_gap = 0.0
    print(
        f'buffer_frames={capacity} rate={rate} sample_interval_ms={period * 500:.1f}',
        flush=True,
    )
    with (substream / 'status').open() as status:
        while True:
            status.seek(0)
            values = {
                key.strip(): value
                for key, _, value in (s.partition(':') for s in status)
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
                    print(
                        f'{timestamp} peak_frames={peak} '
                        f'max_sample_gap_ms={max_sample_gap * 1000:.1f}',
                        flush=True,
                    )
                    next_report = sampled_at + 10
                    peak = 0
                    max_sample_gap = 0.0
            previous_level = level
            sleep(period / 2)


if __name__ == '__main__':
    monitor(tyro.cli(Options).substream)
