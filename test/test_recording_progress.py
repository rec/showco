from __future__ import annotations

import unittest

from showco.runtime import gui_schema, incidents, models, recording_progress, views


class RecordingProgressTests(unittest.TestCase):
    def test_flags_recording_that_stops_advancing(self) -> None:
        times = iter([0.0, 1.0, recording_progress.STALL_SECONDS + 2])
        monitor = recording_progress.ProgressMonitor(lambda: next(times))

        self.assertFalse(monitor.observe(recs(1.0)).ok)
        self.assertTrue(monitor.observe(recs(2.0)).ok)
        self.assertFalse(monitor.observe(recs(2.0)).ok)

    def test_pause_and_session_reset_require_new_progress(self) -> None:
        monitor = recording_progress.ProgressMonitor(lambda: 0.0)
        monitor.observe(recs(10))
        self.assertTrue(monitor.observe(recs(11)).ok)
        self.assertFalse(monitor.observe(recs(0)).ok)
        self.assertTrue(monitor.observe(recs(1)).ok)
        monitor.observe(recs(1).model_copy(update={'paused': True}))
        self.assertFalse(monitor.observe(recs(1)).ok)


def recs(recorded_seconds: float) -> models.RecsStatus:
    return models.RecsStatus(
        service=models.ServiceStatus(name='recs', state='connected'),
        snapshot_available=True,
        recorded_seconds=recorded_seconds,
    )


def input_channel(
    name: str, number: int, recorded: float, *, requested: bool = True
) -> models.ChannelLevel:
    return models.ChannelLevel(
        name=name,
        device='X18',
        source_key='x18',
        channels=[number],
        state='present',
        recorded_seconds=recorded,
        capture_running=True,
        capture_age_seconds=0.1,
        write_requested=requested,
        writing_enabled=True,
    )


def test_show_flags_one_stalled_input_while_another_keeps_writing() -> None:
    now = [0.0]
    monitor = recording_progress.InputMonitor(lambda: now[0])
    rec = recs(20).model_copy(
        update={
            'session_directory': '/recordings/show',
            'channels': [
                input_channel('Instrument', 1, 10),
                input_channel('Vocal', 2, 10),
                input_channel('Room', 3, 5, requested=False),
            ],
        }
    )
    monitor.observe(rec)
    now[0] = recording_progress.INPUT_STALL_SECONDS + 1
    inputs = monitor.observe(
        rec.model_copy(
            update={
                'channels': [
                    rec.channels[0],
                    input_channel('Vocal', 2, 16),
                    rec.channels[2],
                ]
            }
        )
    )
    show = models.ShowStatus(
        recs=rec,
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
        recording_inputs=inputs,
        recording_progress=models.RecordingProgress(ok=True, message='Audio advancing'),
    )
    page = gui_schema.load_gui(gui_schema.DEFAULT_GUI_PATH).page('show')
    html = views.configured_page(page, show)

    assert 'Incomplete recording on 1 input' in html
    assert '<li class="failed">' in html
    assert 'Instrument (1)' in html
    assert 'writes stalled' in html
    assert 'Writing audio' in html
    assert 'silence filtered' in html
    faults = incidents.fault_details(show)
    assert any(
        'Instrument (1)' in detail and '/recordings/show' in detail
        for _, detail, _ in faults
    )
    assert not inputs[2].fault


def test_new_session_pause_and_lost_status_do_not_reuse_recording_evidence() -> None:
    now = [0.0]
    monitor = recording_progress.InputMonitor(lambda: now[0])
    status = recs(1).model_copy(
        update={
            'instance_token': 'recs-1',
            'session_directory': '/recordings/first',
            'channels': [input_channel('Instrument', 1, 0, requested=False)],
            'missing_audio_inputs': ['Never connected'],
        }
    )
    track, missing = monitor.observe(status)
    assert not track.ok and not track.fault and 'no audio saved' in track.message
    assert missing.fault
    assert (
        monitor.observe(status.model_copy(update={'paused': True}))[0].message
        == 'Paused'
    )
    assert (
        monitor.observe(
            status.model_copy(
                update={
                    'service': models.ServiceStatus(name='recs', state='stale'),
                }
            )
        )
        == []
    )
    now[0] = 100
    new = status.model_copy(
        update={
            'session_directory': '/recordings/second',
            'channels': [input_channel('Instrument', 1, 0)],
        }
    )
    assert not monitor.observe(new)[0].fault
    now[0] += 1
    stale = new.channels[0].model_copy(
        update={
            'capture_age_seconds': recording_progress.CAPTURE_STALE_SECONDS + 1,
        }
    )
    inputs = monitor.observe(new.model_copy(update={'channels': [stale]}))
    assert inputs[0].fault and inputs[0].message == 'No fresh samples'
