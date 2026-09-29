import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from showco.runtime import gui_schema, models, views


@pytest.mark.parametrize('connected', [True, False])
def test_initial_and_refreshed_status_use_the_same_text(connected: bool) -> None:
    state = 'connected' if connected else 'offline'
    status = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state=state),
            recording=connected,
            paused=False,
            elapsed_seconds=3672.4 if connected else None,
            file_count=3 if connected else None,
            snapshot_error=None if connected else 'snapshot lost',
            disk=models.RecordingDiskStatus(
                path='/recordings',
                used_bytes=1024**3,
                free_bytes=3 * 1024**3,
                total_bytes=4 * 1024**3,
                estimated_seconds_remaining=3672.4,
            )
            if connected
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
            {'status': status.model_dump(mode='json'), 'expected': expected}
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
