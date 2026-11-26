from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest import mock

import pytest
from reccy.configuration import settings

from showco.runtime import gui_schema, incidents, models, rehearsal, views
from showco.runtime.app import ShowcoApp


class IncidentTimelineTests(unittest.TestCase):
    def test_records_changed_state_after_initial_observation(self) -> None:
        timeline = incidents.IncidentTimeline()
        timeline.observe(status())

        observed = timeline.observe(status(paused=True))

        self.assertEqual(observed[0].message, 'Recording: enabled to paused')


def status(*, paused: bool = False) -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            snapshot_available=True,
            paused=paused,
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
    )


@pytest.mark.parametrize(
    'music_status',
    [
        models.MusicStatus(mode='fault', error='routing failed'),
        models.MusicStatus(state='failed', error='player failed'),
        models.MusicStatus(
            closing=models.ClosingStatus(state='failed', error='credits failed')
        ),
    ],
)
def test_music_faults_reach_live_status_and_record_recovery(
    music_status: models.MusicStatus,
) -> None:
    music = mock.Mock()
    music.status.return_value = music_status
    app = ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        music_controller=music,
    )
    show = app.status()
    fault = next(f for f in show.active_faults if f.name == 'Music')
    assert 'failed' in fault.message
    assert any(i.message.startswith('Music:') for i in show.incidents)
    music.status.return_value = models.MusicStatus(mode='record')
    recovered = app.status()
    assert not any(f.name == 'Music' for f in recovered.active_faults)
    assert any(i.message == 'Music: recovered' for i in recovered.incidents)


def test_empty_observed_baseline_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / 'incidents.json'
    timeline = incidents.IncidentTimeline(path)
    timeline.previous = {}
    timeline.save()

    restored = incidents.IncidentTimeline(path)

    assert restored.previous == {}
    assert restored.load_error is None


def test_current_session_input_failure_survives_panel_restart_while_audio_advances(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = status().recs.model_copy(
        update={
            'session_started_at': datetime.fromisoformat(
                '2026-09-07T12:00:00Z'
            ).timestamp(),
            'recorded_seconds': 12.0,
            'errors': [
                models.ErrorRecord(
                    timestamp='2026-09-07T11:50:00Z', message='old error'
                ),
                models.ErrorRecord(
                    timestamp='2026-09-07T12:10:00Z',
                    message='Device Main instrument failed',
                ),
            ],
        }
    )
    recs = mock.Mock()
    recs.status.side_effect = [base, base.model_copy(update={'recorded_seconds': 13.0})]
    app = ShowcoApp(
        recs,
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
    )
    app.run_started_at = datetime.fromisoformat('2026-09-07T12:15:00Z').timestamp()

    app.status()
    show = app.status()

    assert show.recording_progress.ok
    assert [e.message for e in show.recs.errors] == ['Device Main instrument failed']
    assert any(f.message == 'Device Main instrument failed' for f in show.active_faults)
    assert (
        caplog.messages.count('recs recording errors: Device Main instrument failed')
        == 1
    )


def test_changed_recording_fault_requires_a_new_acknowledgement() -> None:
    timeline = incidents.IncidentTimeline()
    show = status()
    first = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={
                    'errors': [
                        models.ErrorRecord(timestamp='', message='Device A failed')
                    ]
                }
            )
        }
    )
    timeline.observe(first)
    acknowledged = timeline.faults[0]
    timeline.acknowledge(acknowledged.name, acknowledged.started_at.isoformat())
    changed = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={
                    'errors': [
                        *first.recs.errors,
                        models.ErrorRecord(timestamp='', message='Device B failed'),
                    ]
                }
            )
        }
    )

    timeline.observe(changed)

    assert 'Device B failed' in timeline.faults[0].message
    assert not timeline.faults[0].acknowledged
    with pytest.raises(ValueError, match='no longer current'):
        timeline.acknowledge(acknowledged.name, acknowledged.started_at.isoformat())


def test_incident_write_failure_is_logged_once_and_visible_on_show(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        state_directory=tmp_path,
    )
    with mock.patch.object(
        settings, 'write_json_model', side_effect=OSError('full disk')
    ):
        app.status()
        with app.status_storage_lock:
            pass
        show = app.status()
        with app.status_storage_lock:
            pass

    page = gui_schema.load_gui(gui_schema.DEFAULT_GUI_PATH).page('show')
    fragment = views.configured_page(page, show)
    assert 'Cannot save incident history: full disk' in fragment
    assert (
        sum(
            m.startswith('Show monitoring: Cannot save incident history: full disk')
            for m in caplog.messages
        )
        == 1
    )


@pytest.mark.parametrize('filename', ['incidents.json', 'soundcheck.json'])
def test_stalled_status_storage_does_not_hide_capture_fault_or_block_show_controls(
    tmp_path: Path, filename: str
) -> None:
    writing = Event()
    release = Event()
    recs = mock.Mock()
    recs.status.return_value = status().recs
    music = mock.Mock()
    music.status.return_value = models.MusicStatus(mode='record')
    music.record.return_value = music.stop.return_value = models.ActionResult(
        ok=True, message='accepted'
    )
    app = ShowcoApp(
        recs,
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        music_controller=music,
        state_directory=tmp_path,
    )
    write_json = settings.write_json_model

    def write(path: Path, model: object, **kwargs: object) -> None:
        if path.name == filename:
            writing.set()
            assert release.wait(5)
        write_json(path, model, **kwargs)

    with mock.patch.object(settings, 'write_json_model', side_effect=write):
        app.status()
        try:
            assert writing.wait(2)
            recs.status.return_value = status().recs.model_copy(
                update={'service': models.ServiceStatus(name='recs', state='offline')}
            )
            with mock.patch('showco.runtime.app.STATUS_STORAGE_WARNING_SECONDS', 0):
                app.status()
                live = app.http_status()
            assert live is not None
            assert any(
                f.name == 'recs' and f.message == 'offline' for f in live.active_faults
            )
            assert 'storage is stalled' in (live.monitoring_error or '')
            assert app.run_action({'action': 'music-record'}).ok
            assert app.run_action(
                {'action': 'music-stop', 'confirmation': 'shutdown'}
            ).ok
        finally:
            release.set()
            with app.status_storage_lock:
                pass
