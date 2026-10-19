from __future__ import annotations

import unittest
from unittest import mock

import pytest

from showco.runtime import incidents, models, rehearsal
from showco.runtime.app import ShowcoApp


class IncidentTimelineTests(unittest.TestCase):
    def test_records_changed_state_after_initial_observation(self) -> None:
        timeline = incidents.IncidentTimeline()
        timeline.observe(status())

        observed = timeline.observe(status(paused=True))

        self.assertEqual(observed[0].message, 'Recording: recording to paused')


def status(*, paused: bool = False) -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            recording=True,
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
