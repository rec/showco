import json
import tarfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from showco.runtime import models, recovery, rehearsal, setlist, soundcheck, workflows
from showco.runtime.server import ShowcoApp, ShowcoHandler


def status() -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            recorded_seconds=10,
            channels=[
                models.ChannelLevel(
                    name='Vocal', device='X18', channels=[1], state='present', on=True
                )
            ],
            disk=models.RecordingDiskStatus(
                path='/recordings', used_bytes=10, free_bytes=90, total_bytes=100
            ),
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
    )


def application(tmp_path: Path) -> ShowcoApp:
    return ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        state_directory=tmp_path,
    )


def test_cues_survive_restart_and_stale_requests_do_not_repeat_markers(
    tmp_path: Path,
) -> None:
    controller = setlist.SetListController(tmp_path / 'setlist.json')
    marker = mock.Mock(return_value=models.ActionResult(ok=True, message='ok'))
    songs = [
        setlist.Song(title='First', notes='Quiet intro', seconds=180),
        setlist.Song(title='Skip'),
        setlist.Song(title='Last'),
    ]
    controller.act('setlist-save', 0, marker, songs)
    controller.act('setlist-next', 1, marker)
    with pytest.raises(ValueError, match='changed'):
        controller.act('setlist-next', 1, marker)
    controller.act('setlist-skip', controller.state.revision, marker)
    controller.act('setlist-repeat', controller.state.revision, marker)
    controller.act('setlist-interval', controller.state.revision, marker)
    restored = setlist.SetListController(controller.path)
    assert restored.state.songs == songs
    assert restored.state.interval
    assert restored.state.next_index == 2
    assert restored.state.started_at is not None
    restored.act('setlist-next', restored.state.revision, marker)
    assert marker.call_args_list == [
        mock.call('song start: First'),
        mock.call('song start: First'),
        mock.call('interval'),
        mock.call('song start: Last'),
    ]


def test_uncertain_cue_is_persisted_before_send_and_never_replayed(
    tmp_path: Path,
) -> None:
    controller = setlist.SetListController(tmp_path / 'setlist.json')
    controller.act('setlist-save', 0, mock.Mock(), [setlist.Song(title='Song')])

    def timeout(label: str) -> models.ActionResult:
        assert setlist.SetListController(controller.path).state.pending.label == label
        raise TimeoutError('lost reply')

    with pytest.raises(TimeoutError):
        controller.act('setlist-next', 1, timeout)
    restored = setlist.SetListController(controller.path)
    marker = mock.Mock()
    with pytest.raises(ValueError, match='uncertain'):
        restored.act('setlist-next', restored.state.revision, marker)
    restored.act('setlist-accept', restored.state.revision, marker)
    marker.assert_not_called()
    assert restored.state.current == 0
    assert 'unverified' in restored.state.message


def test_cue_storage_failure_prevents_send(tmp_path: Path) -> None:
    controller = setlist.SetListController(tmp_path / 'setlist.json')
    marker = mock.Mock()
    with mock.patch.object(Path, 'write_text', side_effect=OSError('full disk')):
        with pytest.raises(OSError):
            controller.act('setlist-interval', 0, marker)
    marker.assert_not_called()


def test_failed_cue_requires_explicit_retry() -> None:
    controller = setlist.SetListController()
    marker = mock.Mock(return_value=models.ActionResult(ok=False, message='offline'))
    assert not controller.act('setlist-interval', 0, marker).ok
    assert controller.state.pending is not None
    marker.return_value = models.ActionResult(ok=True, message='ok')
    assert controller.act('setlist-retry', controller.state.revision, marker).ok
    assert marker.call_count == 2


@pytest.mark.parametrize(
    'change', ['day', 'mount', 'configuration', 'inputs', 'service']
)
def test_soundcheck_evidence_invalidates_when_conditions_change(
    tmp_path: Path, change: str
) -> None:
    identity = mock.Mock(return_value='mount-1')
    check = soundcheck.Soundcheck(tmp_path / 'soundcheck.json', identity)
    show = status()
    now = datetime.now().astimezone()
    check.observe(show, 'config', now)
    check.begin(['X18: 1'])
    assert check.check('inputs', show).ok
    if change == 'mount':
        identity.return_value = 'mount-2'
    if change == 'inputs':
        show = show.model_copy(
            update={'recs': show.recs.model_copy(update={'channels': []})}
        )
    if change == 'service':
        show = show.model_copy(
            update={
                'recs': show.recs.model_copy(
                    update={
                        'service': models.ServiceStatus(name='recs', state='failed')
                    }
                )
            }
        )
    check.observe(
        show,
        'changed' if change == 'configuration' else 'config',
        now + timedelta(days=1) if change == 'day' else now,
    )
    restored = soundcheck.Soundcheck(check.path, identity)
    assert restored.state.results['inputs'].state == 'invalid'


def test_sample_writes_are_not_proof_of_playback() -> None:
    check = soundcheck.Soundcheck(identity=lambda path: 'mount')
    show = status()
    check.begin(['X18: 1'])
    check.record_start(show)
    assert not check.check('recording', show).ok
    advanced = show.model_copy(
        update={'recs': show.recs.model_copy(update={'recorded_seconds': 12})}
    )
    assert check.check('recording', advanced).ok
    assert not check.check('playback', advanced).ok
    assert check.check(
        'playback', advanced, confirmation=True, note='Session 7, vocal'
    ).ok
    check.record_start(advanced)
    assert 'playback' not in check.state.results
    assert not check.check(
        'playback', advanced, confirmation=True, note='old sample'
    ).ok


def test_soundcheck_missing_identity_and_bad_inputs_cannot_pass() -> None:
    check = soundcheck.Soundcheck(identity=lambda path: '')
    show = status()
    check.begin(['X18: 1', 'Missing: 2'])
    assert not check.check('disk', show, confirmation=True).ok
    assert not check.check('inputs', show).ok
    assert check.check('lights', show, skip=True, note='No lights tonight').ok
    assert check.state.results['lights'].state == 'skipped'
    with pytest.raises(ValueError, match='reason'):
        check.check('stream', show, skip=True)


def test_soundcheck_pages_and_status_never_start_output(tmp_path: Path) -> None:
    app = application(tmp_path)
    with mock.patch.object(app.recs, 'action') as action:
        payload = workflows.status(app)
        expected = [x.key for x in payload.inputs]
        result = app.run_action(
            {
                'action': 'soundcheck-begin',
                'scope': payload.soundcheck.scope,
                'expected': json.dumps(expected),
            }
        )
        assert result.ok
        assert not app.run_action(
            {'action': 'soundcheck-start-recording', 'scope': payload.soundcheck.scope}
        ).ok
        assert not app.run_action(
            {
                'action': 'soundcheck-start-recording',
                'scope': 'stale',
                'confirmation': 'output',
            }
        ).ok
    action.assert_not_called()


def test_recovery_verifies_separately_without_restarting_other_services(
    tmp_path: Path,
) -> None:
    controller = recovery.Recovery(tmp_path / 'recovery.json')
    show = status()
    failed = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={
                    'service': models.ServiceStatus(
                        name='recs', state='failed', last_error='lost RPC'
                    )
                }
            )
        }
    )
    restart = mock.Mock(
        return_value=models.ActionResult(ok=True, message='restart requested')
    )
    remember = mock.Mock()
    controller.restart('recs', 0, failed, restart, remember)
    assert controller.state.outcome == 'checking'
    restored = recovery.Recovery(controller.path)
    with pytest.raises(ValueError, match='already handled'):
        restored.restart('recs', 0, failed, restart, remember)
    with pytest.raises(ValueError, match='Verify'):
        restored.restart('recs', 1, failed, restart, remember)
    assert not restored.verify(failed, remember).ok
    assert restored.verify(show, remember).ok
    assert restored.state.outcome == 'recovered'
    assert restored.state.original_failure == 'lost RPC'
    restart.assert_called_once_with('recs')
    assert 'original failure: lost RPC' in remember.call_args_list[0].args[0]


@pytest.mark.parametrize('service', ['recs', 'streamo', 'unknown'])
def test_recovery_rejects_healthy_disabled_and_unknown_services(service: str) -> None:
    restart = mock.Mock()
    with pytest.raises(ValueError):
        recovery.Recovery().restart(service, 0, status(), restart, mock.Mock())
    restart.assert_not_called()


def test_recovery_needs_named_confirmation(tmp_path: Path) -> None:
    app = application(tmp_path)
    app.recovery_restart = mock.Mock()
    assert not app.run_action(
        {'action': 'recovery-restart', 'service': 'recs', 'revision': '0'}
    ).ok
    app.recovery_restart.assert_not_called()


def test_marker_sent_before_final_write_failure_remains_uncertain(
    tmp_path: Path,
) -> None:
    controller = setlist.SetListController(tmp_path / 'setlist.json')
    write = Path.write_text
    calls = 0

    def fail_second_write(path: Path, content: str) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('full disk')
        return write(path, content)

    marker = mock.Mock(return_value=models.ActionResult(ok=True, message='ok'))
    with mock.patch.object(Path, 'write_text', fail_second_write):
        with pytest.raises(OSError):
            controller.act('setlist-interval', 0, marker)
    marker.assert_called_once_with('interval')
    assert setlist.SetListController(controller.path).state.pending is not None


def test_background_observation_invalidates_soundcheck_without_workflow_page(
    tmp_path: Path,
) -> None:
    app = application(tmp_path)
    identity = mock.Mock(return_value='first mount')
    app.soundcheck.identity = identity
    app.status()
    app.soundcheck.begin(['X18: 1'])
    app.soundcheck.check('lights', status(), skip=True, note='unused')
    identity.return_value = 'different mount'
    app.status()
    assert app.soundcheck.state.results['lights'].state == 'invalid'


def test_failed_invalidation_stays_invalid_in_memory_and_retries_save(
    tmp_path: Path,
) -> None:
    check = soundcheck.Soundcheck(tmp_path / 'soundcheck.json', lambda path: 'mount')
    check.observe(status(), 'config')
    check.begin(['X18: 1'])
    check.check('inputs', status())
    with mock.patch.object(Path, 'write_text', side_effect=OSError('full disk')):
        with pytest.raises(OSError):
            check.invalidate('changed')
    assert check.state.results['inputs'].state == 'invalid'
    check.observe(status(), 'config')
    assert soundcheck.Soundcheck(check.path).state.results['inputs'].state == 'invalid'


def test_failed_restart_is_not_reported_as_recovery() -> None:
    show = status()
    failed = show.model_copy(
        update={
            'recs': show.recs.model_copy(
                update={'service': models.ServiceStatus(name='recs', state='failed')}
            )
        }
    )
    controller = recovery.Recovery()
    restart = mock.Mock(
        return_value=models.ActionResult(ok=False, message='restart failed')
    )
    assert not controller.restart('recs', 0, failed, restart, mock.Mock()).ok
    assert controller.state.outcome == 'failed'
    assert not controller.verify(failed, mock.Mock()).ok
    restart.assert_called_once_with('recs')


@pytest.mark.parametrize(
    'route', ['setlist', 'soundcheck', 'recovery', 'workflow-status']
)
def test_workflow_routes_are_read_only(tmp_path: Path, route: str) -> None:
    handler = object.__new__(ShowcoHandler)
    handler.app = application(tmp_path)
    handler.path = '/' + route
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler.wfile = BytesIO()
    with mock.patch.object(handler.app.recs, 'action') as action:
        handler._do_get()
    action.assert_not_called()
    handler.send_response.assert_called_once_with(200)
    assert handler.wfile.getvalue()


def test_diagnostics_download_uses_existing_collector(tmp_path: Path) -> None:
    handler = object.__new__(ShowcoHandler)
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler.wfile = BytesIO()
    (tmp_path / 'bundle.json').write_text('{}')
    with mock.patch(
        'showco.runtime.workflows.bundle.create_bundle', return_value=tmp_path
    ) as collect:
        workflows.download(handler)
    collect.assert_called_once()
    handler.send_header.assert_any_call('Cache-Control', 'no-store')
    with tarfile.open(
        fileobj=BytesIO(handler.wfile.getvalue()), mode='r:gz'
    ) as archive:
        assert './bundle.json' in archive.getnames()
