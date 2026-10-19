import html
import json
import re
import subprocess
from html.parser import HTMLParser
from itertools import product
from pathlib import Path

import pytest

from showco.runtime import gui_schema, models, views


@pytest.mark.parametrize(
    'connected,snapshot_available,paused,variant',
    [(c, s, p, 'normal') for c, s, p in product([True, False], repeat=3)]
    + [
        (True, True, False, v)
        for v in ['advancing', 'stale-disk', 'no-disk', 'closing', 'protected']
    ],
)
def test_initial_and_refreshed_status_use_the_same_text(
    connected: bool, snapshot_available: bool, paused: bool, variant: str
) -> None:
    state = 'connected' if connected else 'offline'
    status = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state=state),
            snapshot_available=snapshot_available,
            paused=paused,
            elapsed_seconds=3672.4 if connected else None,
            file_count=3 if connected else None,
            snapshot_error=None if connected else 'snapshot lost',
            disk_error='disk probe unavailable' if variant == 'stale-disk' else None,
            disk=models.RecordingDiskStatus(
                path='/recordings',
                used_bytes=1024**3,
                free_bytes=3 * 1024**3,
                total_bytes=4 * 1024**3,
                estimated_seconds_remaining=3672.4,
            )
            if connected and variant != 'no-disk'
            else None,
            playback=models.PlaybackStatus(
                state='playing',
                session=-1,
                path='/recordings/a.wav',
                source='Mic',
                channel='1',
                output_channel='2',
                position_seconds=65.7,
                duration_seconds=3672.4,
            )
            if connected
            else models.PlaybackStatus(),
            osc=[
                models.RecorderStatus(
                    name='X18',
                    state='running',
                    log_path='X18.jsonl',
                    log_size=12,
                )
            ],
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state=state),
            stream_state='live' if connected else 'stopped',
            muted=connected,
            output_bitrate_kbps=128 if connected else None,
        ),
        lyte=models.LyteStatus(
            service=models.ServiceStatus(name='lyte', state=state),
            active_animation='waves' if connected else None,
            queued_animation='stars' if connected else None,
            bindings={'fader': 'master'} if connected else {},
            midi_connected=connected,
            strings={
                'stage': models.LyteStringStatus(
                    state='streaming',
                    host='10.0.0.2',
                    frame_count=42,
                )
            }
            if connected
            else {},
        ),
        system=models.SystemStatus(
            temperature_c=42.5 if connected else None,
            temperature_error=None if connected else 'sensor unavailable',
            cpu_percent=52 if connected else None,
            memory_used_bytes=1024**3 if connected else None,
            memory_total_bytes=4 * 1024**3 if connected else None,
        ),
        recording_progress=models.RecordingProgress(
            ok=True, message='audio writes advancing'
        )
        if variant == 'advancing'
        else models.RecordingProgress(),
        music=models.MusicStatus(mode='closing' if variant == 'closing' else 'stopped'),
        recording_controls_available=variant != 'closing',
        performance_locked=variant == 'protected',
        mixers=[
            models.MixerStatus(
                name='X18',
                state='reachable',
                audio_ready=False,
                midi_ready=True,
                latency_ms=3.25,
            )
        ],
    )
    actions = ActionButtons()
    actions.feed(
        views.configured_page(gui_schema.current_gui().page('actions'), status)
    )
    health = views.configured_page(gui_schema.current_gui().page('health'), status)
    playback = views.configured_page(gui_schema.current_gui().page('playback'), status)
    expected = {
        name: text(health, name)
        for name in (
            'readiness-state',
            'recs-health',
            'recs-snapshot',
            'recording-progress',
            'streamo-health',
            'lyte-health',
            'temperature',
            'bitrate',
            'recording-detail',
            'streaming-detail',
            'cpu-value',
            'memory-value',
            'disk-value',
        )
    }
    expected.update(
        {
            name: text(playback, name)
            for name in ('playback-state', 'playback-selection', 'playback-position')
        }
    )
    expected['mixer-detail'] = formatted_text(health, 'mixer_detail')
    expected['osc-detail'] = formatted_text(health, 'osc_detail')
    subprocess.run(
        ['node', 'test/browser_presentation.cjs'],
        input=json.dumps(
            {
                'status': status.model_dump(mode='json'),
                'expected': expected,
                'actions': actions.buttons,
            }
        ),
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=10,
    )


def text(page: str, name: str) -> str:
    match = re.search(rf'id="{name}"[^>]*>(.*?)</(?:p|span)>', page, re.S)
    assert match is not None, name
    return html.unescape(re.sub(r'<[^>]+>', '', match.group(1))).strip()


def formatted_text(page: str, format_name: str) -> str:
    match = re.search(rf'data-format="{format_name}">(.*?)</span>', page, re.S)
    assert match is not None, format_name
    return html.unescape(match.group(1)).strip()


class ActionButtons(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form: dict[str, str | None] = {}
        self.buttons: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == 'form':
            self.form = values
        if tag == 'button' and 'data-disabled-when' in self.form:
            self.buttons.append(
                {
                    'action': self.form['data-action'],
                    'disabledWhen': self.form['data-disabled-when'],
                    'disabled': 'disabled' in values,
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == 'form':
            self.form = {}
