import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from streamo import managed

from showco.runtime import models, rehearsal, soundcheck, twitch, workflows
from showco.runtime.app import ShowcoApp


@pytest.fixture
def ready() -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            snapshot_available=True,
            instance_token='daemon',
            session_directory='/recordings/2026/11/26/12-00-00',
            disk=models.RecordingDiskStatus(
                path='/recordings',
                used_bytes=1,
                free_bytes=99,
                total_bytes=100,
                on_system_disk=False,
            ),
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='connected'),
            stream_state='stopped',
        ),
    )


def test_readiness_reports_recording_disk_clock_and_sample_blockers(
    ready: models.ShowStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(twitch, 'clock_is_synchronized', lambda: False)
    assert ready.recs.disk is not None
    status = ready.model_copy(
        update={
            'recs': ready.recs.model_copy(
                update={
                    'paused': True,
                    'disk': ready.recs.disk.model_copy(update={'on_system_disk': True}),
                }
            )
        }
    )
    blockers = twitch.TwitchReadiness(tmp_path / 'streamo.toml').check(
        status, soundcheck.Soundcheck(), None
    )
    assert blockers == [
        'Resume recs recording',
        'Mount and check the intended recording disk',
        'Waiting for recent Pi clock synchronization',
        'Verify the recording disk and a saved sample in Soundcheck',
    ]


def test_clock_requires_a_recent_successful_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / 'synchronized'
    monkeypatch.setattr(twitch, 'CLOCK_SYNC_MARKER', marker)
    synchronized = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'yes\n'))
    monkeypatch.setattr(twitch.subprocess, 'run', synchronized)
    assert not twitch.clock_is_synchronized()
    synchronized.assert_not_called()
    marker.touch()
    assert twitch.clock_is_synchronized()
    old = marker.stat().st_mtime - twitch.CLOCK_SYNC_MAX_AGE_SECONDS - 1
    os.utime(marker, (old, old))
    assert not twitch.clock_is_synchronized()


def test_twitch_checks_current_capture_and_selected_subscription(
    ready: models.ShowStatus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / 'streamo.toml'
    config_path.touch()
    sample = soundcheck.Soundcheck(identity=lambda path: 'mount')
    track = models.ChannelLevel(
        name='Main', device='X18', channels=[17, 18], state='silent', recorded_seconds=0
    )
    show = ready.model_copy(
        update={'recs': ready.recs.model_copy(update={'channels': [track]})}
    )
    sample.observe(show, workflows.soundcheck_configuration(None))
    sample.begin(['X18: 17,18'])
    assert sample.check('disk', show, confirmation=True).ok
    sample.record_start(show, {'X18': 'capture'})
    show = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={'channels': [track.model_copy(update={'recorded_seconds': 2})]}
            )
        }
    )
    assert sample.check('recording', show, capture_clocks={'X18': 'capture'}).ok
    assert sample.check('playback', show, confirmation=True, note='Listened to Main').ok

    monkeypatch.setattr(twitch, 'clock_is_synchronized', lambda: True)
    monkeypatch.setattr(
        twitch.settings,
        'read',
        lambda path: {
            'argv': [
                'daemon',
                'run',
                '--config',
                str(config_path.resolve()),
                '--managed',
            ],
            'module': 'streamo',
            'platform': 'linux',
            'control_endpoint': '/run/control',
        },
    )
    source = {'source': 'X18', 'clock_id': 'capture', 'recording_enabled': True}
    connection = mock.Mock()
    inspection = mock.Mock(
        instance=mock.Mock(start_token='daemon'),
        state='idle',
        enabled=True,
        paused=False,
        muted=False,
    )
    connection.inspect.return_value = inspection
    connection.client.call.return_value = {
        'enabled': True,
        'paused': False,
        'sources': [source],
    }
    configured_pair = mock.Mock(daemon=True, source='X18', channels=[17, 18])
    monkeypatch.setattr(
        twitch,
        'load_config',
        lambda path, **kwargs: mock.Mock(recs_input=configured_pair),
    )
    connect = mock.Mock(return_value=connection)
    monkeypatch.setattr(twitch, 'RecsConnection', connect)
    monkeypatch.setattr(twitch, 'check_media', lambda config: [])
    monkeypatch.setattr(
        twitch, 'check_encoders', lambda config: mock.Mock(status='pass')
    )
    receiver = mock.MagicMock()
    receiver.__enter__.return_value.receive.return_value = (
        mock.Mock(input_status=''),
        b'',
    )
    monkeypatch.setattr(twitch, 'AudioClient', mock.Mock(return_value=receiver))

    gate = twitch.TwitchReadiness(config_path)
    assert gate.check(show, sample, None) == []
    connect.assert_called_once_with(configured_pair)
    connection.subscribe.assert_called_once_with(inspection)
    connection.cancel.assert_called_once_with(
        connection.subscribe.return_value.subscription.subscription_id
    )

    source['clock_id'] = 'different-capture'
    assert gate.check(show, sample, None) == [
        'A recs capture changed; verify a new sample in Soundcheck'
    ]
    assert connection.subscribe.call_count == 1

    sample.allow_playback_pause(show, {'X18': 'capture'})
    paused = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={'paused': True, 'offline_sources': ['X18']}
            ),
        }
    )
    sample.observe(paused, workflows.soundcheck_configuration(None))
    assert gate.check(paused, sample, None) == ['Resume recs recording']
    assert gate.check(show, sample, None) == []
    assert gate.capture_clocks == {'X18': 'different-capture'}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [1000.0]
    monkeypatch.setattr(twitch.time, 'monotonic', lambda: clock[0])
    return clock


def make_app(
    ready: models.ShowStatus,
    state: Path,
    gate: mock.Mock,
    launch: mock.Mock,
) -> ShowcoApp:
    gate.config_sha256 = '0' * 64
    gate.config_digest.return_value = '0' * 64
    recs = mock.Mock()
    recs.status.return_value = ready.recs
    streamo = mock.Mock()
    streamo.status.return_value = ready.streamo
    application = ShowcoApp(
        recs,
        streamo,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        streamo_restart=launch,
        streamo_stop=mock.Mock(
            return_value=models.ActionResult(ok=True, message='Stopped')
        ),
        twitch_readiness=gate,
        twitch_state=state if os.name != 'nt' else None,
    )
    application.soundcheck.identity = lambda path: 'mount'
    application.soundcheck.observe(
        ready, workflows.soundcheck_configuration(application.revision)
    )
    return application


def test_twitch_only_starts_after_a_fresh_explicit_request_and_recovers_three_times(
    ready: models.ShowStatus, tmp_path: Path, clock: list[float]
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    path = tmp_path / 'broadcast.json'
    application = make_app(ready, path, gate, launch)
    request = {'action': 'streamo-start'}

    def observe(show: models.ShowStatus = ready) -> None:
        clock[0] += 10
        application.observe_twitch(show)

    observe()
    launch.assert_not_called()
    gate.check.return_value = ['Selected recs audio stalled']
    observe()
    assert application.status().twitch_toggle_available
    assert application.run_action(request).ok
    assert not application.run_action(request).ok
    launch.assert_called_once()

    failed = ready.model_copy(
        update={'streamo': ready.streamo.model_copy(update={'stream_state': 'failed'})}
    )
    assert application.streamo is not None
    application.streamo.status.return_value = failed.streamo
    # A failed prerequisite leaves the full recovery budget intact.
    gate.check.return_value = ['Clock synchronization lost']
    observe(failed)
    launch.assert_called_once()
    gate.check.return_value = []
    observe(failed)
    assert launch.call_count == 2
    observe(failed)
    assert launch.call_count == 3
    live = ready.model_copy(
        update={
            'streamo': ready.streamo.model_copy(
                update={'stream_state': 'streaming', 'ffmpeg_alive': True}
            )
        }
    )
    observe(live)
    observe(failed)
    observe(failed)
    assert launch.call_count == 4
    assert 'recovery exhausted' in application.status().twitch_message
    assert application.run_action(request).ok
    assert launch.call_count == 5


def test_stop_cancels_recovery_during_readiness_probe(
    ready: models.ShowStatus, tmp_path: Path, clock: list[float]
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    path = tmp_path / 'broadcast.json'
    application = make_app(ready, path, gate, launch)
    assert application.run_action({'action': 'streamo-start'}).ok
    failed = ready.model_copy(
        update={'streamo': ready.streamo.model_copy(update={'stream_state': 'failed'})}
    )
    assert application.streamo is not None
    application.streamo.status.return_value = failed.streamo

    def stop_during_probe(*args: object) -> list[str]:
        assert application.run_action({'action': 'streamo-stop'}).ok
        return []

    gate.check.side_effect = stop_during_probe
    clock[0] += 10
    application.observe_twitch(failed)
    assert not path.exists()
    launch.assert_called_once()
    restored = make_app(failed, path, gate, launch)
    gate.check.side_effect = None
    restored.observe_twitch(failed)
    launch.assert_called_once()


def test_changed_session_between_probe_and_recovery_refuses_to_launch(
    ready: models.ShowStatus,
    tmp_path: Path,
    clock: list[float],
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    application = make_app(ready, tmp_path / 'broadcast.json', gate, launch)
    assert application.run_action({'action': 'streamo-start'}).ok
    failed = ready.streamo.model_copy(update={'stream_state': 'failed'})
    assert application.streamo is not None
    application.streamo.status.return_value = failed
    application.recs.status = mock.Mock(
        side_effect=[
            ready.recs,
            ready.recs.model_copy(update={'session_directory': '/new-session'}),
        ]
    )
    clock[0] += 10
    application.observe_twitch(ready.model_copy(update={'streamo': failed}))
    launch.assert_called_once()


def test_operator_can_retry_while_automatic_recovery_is_blocked(
    ready: models.ShowStatus, tmp_path: Path, clock: list[float]
) -> None:
    gate = mock.Mock()
    gate.check.return_value = ['Soundcheck required for recovery']
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    application = make_app(ready, tmp_path / 'broadcast.json', gate, launch)
    assert application.run_action({'action': 'streamo-start'}).ok
    failed = ready.streamo.model_copy(update={'stream_state': 'failed'})
    assert application.streamo is not None
    application.streamo.status.return_value = failed
    clock[0] += 10
    application.observe_twitch(ready.model_copy(update={'streamo': failed}))
    status = application.status()
    assert status.twitch_toggle_available
    assert status.twitch_action == 'streamo-start'
    assert 'Automatic recovery waiting' in status.twitch_message
    assert application.run_action({'action': status.twitch_action}).ok
    assert launch.call_count == 2


def test_clean_service_stop_does_not_authorize_an_automatic_restart(
    ready: models.ShowStatus, tmp_path: Path, clock: list[float]
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    path = tmp_path / 'broadcast.json'
    application = make_app(ready, path, gate, launch)
    assert application.run_action({'action': 'streamo-start'}).ok
    clock[0] += 10
    application.observe_twitch(ready)
    assert not path.exists()
    launch.assert_called_once()


@pytest.mark.skipif(os.name == 'nt', reason='Managed publishing uses POSIX file locks')
@pytest.mark.parametrize('automatic', [False, True])
def test_stop_revokes_managed_permission_while_start_holds_action_lock(
    ready: models.ShowStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: list[float],
    automatic: bool,
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock()
    path = tmp_path / 'broadcast.json'
    monkeypatch.setattr(managed, 'launch_permit_path', lambda: path)
    application = make_app(ready, path, gate, launch)

    if automatic:
        launch.return_value = models.ActionResult(ok=True, message='Started')
        assert application.run_action({'action': 'streamo-start'}).ok
        launch.reset_mock()

    def launch_with_concurrent_stop() -> models.ActionResult:
        assert managed.read_launch_permit().config_sha256 == '0' * 64
        assert application.run_action({'action': 'streamo-stop'}).ok
        with pytest.raises(managed.ManagedLaunchError):
            managed.read_launch_permit()
        return models.ActionResult(ok=True, message='Started')

    launch.side_effect = launch_with_concurrent_stop
    if automatic:
        failed = ready.streamo.model_copy(update={'stream_state': 'failed'})
        assert application.streamo is not None
        application.streamo.status.return_value = failed
        clock[0] += 10
        application.observe_twitch(ready.model_copy(update={'streamo': failed}))
    else:
        result = application.run_action({'action': 'streamo-start'})
        assert not result.ok
        assert 'start canceled' in result.message
    launch.assert_called_once()
    assert application.twitch.run is None


@pytest.mark.skipif(os.name == 'nt', reason='Managed publishing uses POSIX file locks')
def test_failed_unlink_cannot_restore_a_revoked_twitch_run(
    ready: models.ShowStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = mock.Mock()
    gate.check.return_value = []
    launch = mock.Mock(return_value=models.ActionResult(ok=True, message='Started'))
    path = tmp_path / 'broadcast.json'
    monkeypatch.setattr(managed, 'launch_permit_path', lambda: path)
    application = make_app(ready, path, gate, launch)
    assert application.run_action({'action': 'streamo-start'}).ok
    with mock.patch.object(Path, 'unlink', side_effect=PermissionError):
        assert application.run_action({'action': 'streamo-stop'}).ok
    assert path.exists()
    with pytest.raises(managed.ManagedLaunchError):
        managed.read_launch_permit()
    failed = ready.model_copy(
        update={
            'streamo': ready.streamo.model_copy(update={'stream_state': 'failed'}),
        }
    )
    restored = make_app(failed, path, gate, launch)
    restored.observe_twitch(failed)
    launch.assert_called_once()
