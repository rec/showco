from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from showco.runtime import models
from showco.runtime.views import (
    ERROR_PAGE_LIMIT,
    actions_page,
    attributes_page,
    channels_page,
    errors_page,
    health_page,
    playback_page,
)


class ViewsTests(unittest.TestCase):
    def test_status_pages_have_five_page_navigation(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='offline')
                ),
            )
        )

        self.assertIn('href="/channels"', html)
        self.assertIn('href="/health"', html)
        self.assertIn('href="/attributes"', html)
        self.assertIn('href="/actions"', html)
        self.assertIn('href="/errors"', html)
        self.assertNotIn('href="/home"', html)

    def test_actions_page_shows_music_modes_when_x18_music_is_configured(self) -> None:
        html = actions_page(
            [],
            music=models.MusicStatus(
                mode='setup', track=Path('/music/setup/intro.mp3')
            ),
        )

        self.assertIn('Music mode', html)
        self.assertIn('Mode: setup. /music/setup/intro.mp3', html)
        self.assertIn('value="music-setup"', html)
        self.assertIn('value="music-record"', html)
        self.assertIn('value="music-teardown"', html)
        self.assertIn('value="music-stop"', html)

    def test_channels_page_has_live_status_elements(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
            )
        )

        self.assertIn('id="channels"', html)
        self.assertIn('new EventSource("/waveforms")', html)
        self.assertIn('function serviceDetail(service)', html)
        self.assertIn('function saveTrackName(form)', html)
        self.assertIn('  const WAVEFORM_SECONDS = 8', html)
        self.assertIn(
            '.levels {\n  grid-template-columns: repeat(3, minmax(0, 1fr));', html
        )
        self.assertIn('id="connection-status"', html)

    def test_health_page_shows_pi_temperature(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
                system=models.SystemStatus(temperature_c=52.75),
            )
        )

        self.assertIn('Pi temperature', html)
        self.assertIn('52.8 °C', html)

    def test_health_page_labels_paused_recording(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    recording=True,
                    paused=True,
                    elapsed_seconds=65,
                    file_count=3,
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
            )
        )

        self.assertIn('paused after 1:05, 3 files', html)

    def test_health_page_shows_performance_meters(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    disk=models.RecordingDiskStatus(
                        path='/recordings',
                        used_bytes=25 * 1024**3,
                        free_bytes=75 * 1024**3,
                        total_bytes=100 * 1024**3,
                        estimated_seconds_remaining=3600,
                    ),
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
                system=models.SystemStatus(
                    cpu_percent=42,
                    memory_used_bytes=2 * 1024**3,
                    memory_total_bytes=8 * 1024**3,
                ),
            )
        )

        self.assertIn('<h2>Performance</h2>', html)
        self.assertIn('id="cpu-meter"', html)
        self.assertIn('id="memory-meter"', html)
        self.assertIn('id="disk-meter"', html)
        self.assertIn('42%', html)
        self.assertIn('2.0 GiB / 8.0 GiB (25%)', html)
        self.assertIn('/recordings: 75.0 GiB free / 100.0 GiB (25% used)', html)
        self.assertIn('setPerformance(', html)

    def test_health_page_shows_recs_snapshot_error(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    snapshot_error='recs status_snapshot failed: slow',
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
            )
        )

        self.assertIn('id="recs-snapshot"', html)
        self.assertIn('recs snapshot: recs status_snapshot failed: slow', html)

    def test_health_page_shows_empty_recs_errors(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
            )
        )

        self.assertIn('<div id="recs-errors"', html)
        self.assertIn('No errors', html)

    def test_health_page_shows_readiness(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
                readiness=models.ReadinessStatus(
                    checks=[
                        models.ReadinessCheck(name='recs', ok=False, message='offline')
                    ]
                ),
            )
        )

        self.assertIn('Service readiness', html)
        self.assertIn('id="readiness-state">not ready', html)
        self.assertIn('recs</b>: offline', html)

    def test_health_page_shows_incidents(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
                incidents=[
                    models.Incident(
                        timestamp=datetime(2026, 9, 10, 12, 0, 0),
                        message='Recording: recording to paused',
                    )
                ],
            )
        )

        self.assertIn('Observed incidents', html)
        self.assertIn('Recording: recording to paused', html)

    def test_health_page_shows_lyte_installation_status(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
                lyte=models.LyteStatus(
                    service=models.ServiceStatus(name='lyte', state='connected'),
                    running=True,
                    active_animation='tree_show',
                    bindings={'light': 'dots + strings'},
                    strings={
                        'dots': models.LyteStringStatus(
                            state='streaming',
                            host='10.0.0.17',
                            led_count=250,
                            frame_count=42,
                        )
                    },
                    midi_connected=True,
                    note=64,
                ),
            )
        )

        self.assertIn('id="lyte-health"', html)
        self.assertIn('animation tree_show', html)
        self.assertIn('light=dots + strings', html)
        self.assertIn('MIDI connected, note 64', html)
        self.assertIn('dots: streaming 10.0.0.17 250 LEDs 42 frames', html)

    def test_health_page_shows_bitrate_and_mixer_latency(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected'),
                    output_bitrate_kbps=312.5,
                ),
                mixers=[
                    models.MixerStatus(name='X18', state='connected', latency_ms=4.25)
                ],
            )
        )

        self.assertIn('Stream bitrate', html)
        self.assertIn('312 kbps', html)
        self.assertIn('X18: connected: 4.2 ms', html)
        self.assertIn('4.2 ms', html)

    def test_health_page_shows_named_osc_recorders(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    osc=[
                        models.RecorderStatus(
                            name='X18',
                            state='running',
                            log_path='X18.jsonl',
                            log_size=12,
                        ),
                        models.RecorderStatus(
                            name='Flow 8',
                            state='error',
                            last_error='unreachable',
                        ),
                    ],
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                ),
            )
        )

        self.assertIn('X18 OSC recorder: running: X18.jsonl (12 bytes)', html)
        self.assertIn('Flow 8 OSC recorder: error: unreachable', html)

    def test_health_page_shows_named_mixer_input_progress(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected')
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
                mixers=[
                    models.MixerStatus(
                        name='Flow 8',
                        state='waiting',
                        audio_ready=False,
                        midi_ready=False,
                    )
                ],
            )
        )

        self.assertIn('Flow 8: waiting for USB audio and MIDI', html)
        self.assertIn('function mixerDetail', html)

    def test_errors_page_shows_recs_errors_without_controls(self) -> None:
        html = errors_page(
            [
                models.ErrorRecord(
                    timestamp='2026-08-13T12:34:56.789Z',
                    message='disk almost full',
                )
            ]
        )

        self.assertIn('disk almost full', html)
        self.assertIn(f'data-limit="{ERROR_PAGE_LIMIT}"', html)
        self.assertNotIn('Show all errors', html)
        self.assertNotIn('type="checkbox" role="switch"', html)

    def test_errors_page_shows_empty_recs_errors(self) -> None:
        html = errors_page([])

        self.assertIn('No errors', html)

    def test_errors_page_limits_previous_errors(self) -> None:
        html = errors_page(
            [
                models.ErrorRecord(
                    timestamp=f'2026-08-13T12:34:{i:02}Z', message=str(i)
                )
                for i in range(ERROR_PAGE_LIMIT + 1)
            ]
        )

        self.assertNotIn('>0</span>', html)
        self.assertIn(f'>{ERROR_PAGE_LIMIT}</span>', html)

    def test_channels_page_has_track_name_editor_for_recs_channels(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    channels=[
                        models.ChannelLevel(
                            name='1',
                            state='healthy',
                            device='Mic',
                            channels=[1],
                            on=True,
                        ),
                        models.ChannelLevel(
                            name='2', state='healthy', device='Mic', channels=[2]
                        ),
                    ],
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
            )
        )

        self.assertIn('name="track_name" value="1"', html)
        self.assertIn('data-saved-track-name="1"', html)
        self.assertIn('class="channel-state indicator-red"', html)
        self.assertIn('aria-label="recording"', html)
        self.assertIn(
            '<canvas class="waveform" aria-label="Live waveform"></canvas>', html
        )
        self.assertIn('<b>1</b>', html)
        self.assertNotIn('Channel 1', html)
        self.assertIn('>•</span>', html)
        self.assertEqual(html.count('id="save-track-names"'), 1)
        self.assertEqual(html.count('id="revert-track-names"'), 1)
        self.assertEqual(html.count('>Save</button>'), 1)
        self.assertEqual(html.count('>Revert</button>'), 1)
        self.assertNotIn('>healthy</span>', html)
        self.assertIn(
            '<label class="stereo"><input type="checkbox">Stereo</label>', html
        )
        self.assertIn(
            '<button class="calibrate-channel" type="button">Calibrate</button>',
            html,
        )

    def test_channels_page_disables_mono_stereo_control_without_right_channel(
        self,
    ) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    channels=[
                        models.ChannelLevel(
                            name='2', state='healthy', device='Mic', channels=[2]
                        )
                    ],
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
            )
        )

        self.assertIn(
            '<label class="stereo"><input type="checkbox" disabled>Stereo</label>',
            html,
        )

    def test_channels_page_checks_stereo_control(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    channels=[
                        models.ChannelLevel(
                            name='1-2',
                            state='healthy',
                            device='Mic',
                            channels=[1, 2],
                        )
                    ],
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
            )
        )

        self.assertIn(
            '<label class="stereo"><input type="checkbox" checked>Stereo</label>',
            html,
        )

    def test_channels_page_shows_not_recording_channel_light(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name='recs', state='connected'),
                    channels=[
                        models.ChannelLevel(name='1', state='healthy', device='Mic'),
                    ],
                ),
                streamo=models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='connected')
                ),
            )
        )

        self.assertIn('class="channel-state indicator-green"', html)
        self.assertIn('aria-label="not recording"', html)

    def test_attributes_page_has_mutable_recs_attributes(self) -> None:
        html = attributes_page(
            [
                models.MutableAttribute(
                    address='recording.noise_floor',
                    value=70.0,
                ),
                models.MutableAttribute(
                    address='recording.record_everything',
                    value=False,
                ),
            ],
        )

        self.assertIn('recs attributes', html)
        self.assertIn('id="mutable-attributes"', html)
        self.assertIn('data-address="recording.noise_floor"', html)
        self.assertIn('type="number" data-value-type="number" value="70.0"', html)
        self.assertIn('type="checkbox" data-value-type="boolean"', html)
        self.assertIn('saveMutableAttribute', html)

    def test_actions_page_has_stream_restart_button(self) -> None:
        html = actions_page([])

        self.assertIn('Restart Stream', html)
        self.assertIn('value="streamo-restart"', html)

    def test_actions_page_hides_stream_controls_when_disabled(self) -> None:
        html = actions_page([], streamo_enabled=False)

        self.assertNotIn('Restart Stream', html)
        self.assertNotIn('value="streamo-mute"', html)

    def test_actions_page_has_recs_protocol_controls(self) -> None:
        html = actions_page([])

        self.assertIn('value="recs-disk-status"', html)

    def test_playback_page_has_transport_controls(self) -> None:
        html = playback_page(models.PlaybackStatus())

        self.assertIn('href="/playback"', html)
        self.assertIn('value="recs-playback-play"', html)
        self.assertIn('value="recs-playback-stop"', html)
        self.assertIn('value="recs-playback-pause"', html)
        self.assertIn('name="seconds" value="-10"', html)
        self.assertIn('name="seconds" value="10"', html)
        self.assertIn('name="offset" value="-1"', html)
        self.assertIn('name="offset" value="1"', html)
        self.assertIn('script', html)

    def test_actions_page_has_show_markers(self) -> None:
        html = actions_page([])

        for label in ['Show start', 'Song start', 'Interval', 'Show end']:
            self.assertIn(f'name="label" value="{label}"', html)
        self.assertIn('value="recs-list-devices"', html)
        self.assertIn('value="recs-new-session"', html)
        self.assertIn('value="recs-pause-recording"', html)
        self.assertIn('value="recs-resume-recording"', html)
        self.assertNotIn('value="recs-stop-recording"', html)
        self.assertNotIn('value="recs-start-recording"', html)
        self.assertIn('value="recs-marker"', html)
        self.assertIn('value="recs-set-noise-floor"', html)
        self.assertIn('value="recs-shutdown"', html)
        self.assertIn('<option value="cancel" selected>Cancel</option>', html)

    def test_actions_page_has_lyte_light_test(self) -> None:
        html = actions_page([])

        self.assertIn('value="lyte-test"', html)
        self.assertIn('>Test lights</button>', html)
        self.assertIn('button.setAttribute("aria-busy", "true")', html)
        self.assertIn('button:active, button[aria-busy="true"]', html)

    def test_actions_page_has_cable_test_defaults(self) -> None:
        html = actions_page([])

        self.assertIn('value="cable-test"', html)
        self.assertIn('name="channels" value="9-14"', html)
        self.assertIn('name="sends" value="1-6"', html)
        self.assertIn('name="duration-seconds"', html)
        self.assertIn('value="3"', html)

    def test_actions_page_shows_action_history_details(self) -> None:
        html = actions_page(
            [
                models.ActionLogEntry(
                    service='recs',
                    command='calibrate',
                    timestamp=datetime(2026, 9, 4, 12, 34, 56, tzinfo=timezone.utc),
                    result=models.ActionResult(ok=False, message='socket unavailable'),
                )
            ]
        )

        self.assertIn('12:34:56', html)
        self.assertIn('recs calibrate: socket unavailable', html)
        self.assertIn('class="failed"', html)

    def test_musicians_page_lists_add_and_edit_forms(self) -> None:
        from recs.musicians import Musician

        from showco.runtime.views import musicians_page

        html = musicians_page(
            {'mike': Musician(name='mike', contacts=['insta:mike'])}, []
        )

        self.assertIn('href="/musicians"', html)
        self.assertIn('value="recs-musician-add"', html)
        self.assertIn('value="recs-musician-edit"', html)
        self.assertIn('insta:mike', html)
