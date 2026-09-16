from pathlib import Path
from unittest import mock

import pytest

from showco.runtime import incidents, models, readiness, rehearsal
from showco.runtime.monitoring import PerformanceMonitor
from showco.runtime.recs_snapshot import SnapshotStatus
from showco.runtime.server import ShowcoApp


def status(connected: bool = True) -> models.ShowStatus:
    result = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(
                name='recs', state='connected' if connected else 'offline'
            ),
            recording=True,
            disk=models.RecordingDiskStatus(
                path='/recordings', used_bytes=0, free_bytes=100, total_bytes=100
            ),
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
        recording_progress=models.RecordingProgress(ok=True, message='advancing'),
    )
    return result.model_copy(update={'readiness': readiness.status(result)})


def test_fault_history_acknowledgment_and_recovery_survive_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'incidents.json'
    timeline = incidents.IncidentTimeline(path)
    timeline.observe(status(False))
    fault = timeline.faults[0]
    for _ in range(5):
        timeline.observe(status(False))
    assert len(timeline.incidents) == 1
    assert timeline.faults[0].started_at == fault.started_at
    serialized = fault.model_dump(mode='json')['started_at']
    timeline.acknowledge(fault.name, serialized)
    restored = incidents.IncidentTimeline(path)
    assert restored.faults[0].acknowledged
    assert restored.faults[0].started_at == fault.started_at
    restored.observe(status())
    assert not restored.faults
    assert any(i.message == 'recs: recovered' for i in restored.incidents)
    restored.observe(status(False))
    assert not restored.faults[0].acknowledged
    with pytest.raises(ValueError, match='no longer current'):
        restored.acknowledge(fault.name, serialized)


def test_history_is_bounded_on_disk(tmp_path: Path) -> None:
    path = tmp_path / 'incidents.json'
    timeline = incidents.IncidentTimeline(path)
    for index in range(150):
        timeline.observe(status(bool(index % 2)))
    assert len(incidents.IncidentTimeline(path).incidents) == 100
    assert path.stat().st_size < 40000


def test_storage_failure_is_visible_and_retried(tmp_path: Path) -> None:
    timeline = incidents.IncidentTimeline(tmp_path / 'incidents.json')
    with mock.patch.object(Path, 'write_text', side_effect=OSError('full disk')):
        timeline.observe(status(False))
    assert 'full disk' in timeline.storage_error
    assert timeline.faults
    timeline.observe(status(False))
    assert timeline.storage_error is None
    assert incidents.IncidentTimeline(timeline.path).faults


def test_background_sampling_observes_services_without_browser_requests(
    tmp_path: Path,
) -> None:
    recs = rehearsal.RehearsalRecsClient()
    monitor = PerformanceMonitor(
        rehearsal.RehearsalSystemMonitor(),
        lambda: SnapshotStatus(),
        directory=tmp_path / 'metrics',
    )
    application = ShowcoApp(
        recs,
        None,
        monitor,
        rehearsal.RehearsalMixersMonitor(),
        state_directory=tmp_path,
    )
    monitor.observe = application.status
    with mock.patch.object(
        recs, 'status', side_effect=[status().recs, status(False).recs]
    ):
        monitor.sample()
        monitor.sample()
    restored = incidents.IncidentTimeline(tmp_path / 'incidents.json')
    assert any(f.name == 'recs' and f.message == 'offline' for f in restored.faults)


def test_service_observation_failure_does_not_end_sampling(tmp_path: Path) -> None:
    monitor = PerformanceMonitor(
        rehearsal.RehearsalSystemMonitor(), lambda: SnapshotStatus(), directory=tmp_path
    )
    monitor.observe = mock.Mock(side_effect=[ConnectionError('offline'), None])
    monitor.sample()
    assert 'offline' in monitor.observation_error
    monitor.sample()
    assert monitor.observation_error is None
    assert len(monitor.samples) == 2
