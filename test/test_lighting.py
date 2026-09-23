import subprocess
from pathlib import Path
from unittest import mock

import pytest

from showco.runtime import gui_schema, lighting, models, rehearsal, views, workflows
from showco.runtime.lyte import LyteClient
from showco.runtime.server import ShowcoApp


def application(path: Path) -> ShowcoApp:
    return ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        lyte=rehearsal.RehearsalLyteClient(),
        state_directory=path,
    )


def act(app: ShowcoApp, action: str, **fields: str) -> models.ActionResult:
    return app.run_action(
        {
            'action': f'lighting-{action}',
            'revision': str(app.lighting.state.revision),
            **fields,
        }
    )


def prepare(app: ShowcoApp) -> None:
    assert act(
        app,
        'save',
        cues='[{"name":"Opening","animation":"circle"},{"name":"Finale","animation":"square"}]',
    ).ok


def test_forward_back_reconnect_and_external_selection(tmp_path: Path) -> None:
    app = application(tmp_path)
    prepare(app)
    assert not act(app, 'back').ok
    stale = str(app.lighting.state.revision)
    assert act(app, 'go').ok
    assert app.lighting.state.current == 0
    assert not act(app, 'go', revision=stale).ok
    assert act(app, 'go').ok
    assert app.lighting.state.current == 1
    assert not act(app, 'go').ok
    assert act(app, 'back').ok
    assert app.lighting.state.current == 0
    assert app.lyte is not None
    assert app.lyte.status().active_animation == 'circle'
    app.lyte.select_animation('idle')
    with mock.patch('showco.runtime.workflows.refresh_soundcheck'):
        for _ in range(2):
            snapshot = workflows.status(app)
            assert snapshot.lighting.current == 0
            assert snapshot.show.lyte.active_animation == 'idle'
    restored = application(tmp_path)
    assert restored.lighting.state == app.lighting.state
    assert restored.lyte is not None
    assert restored.lyte.status().active_animation == 'idle'


def test_uncertain_selection_survives_restart_without_replay(tmp_path: Path) -> None:
    app = application(tmp_path)
    prepare(app)

    def lost_reply(name: str) -> models.ActionResult:
        assert (
            lighting.LightingController(tmp_path / 'lighting.json').state.pending == 0
        )
        raise TimeoutError('reply lost')

    with mock.patch.object(
        app.lyte, 'select_animation', side_effect=lost_reply
    ) as select:
        assert not act(app, 'go').ok
        assert app.lighting.state.current == -1
        assert not act(app, 'go').ok
        assert not act(app, 'save', cues='[]').ok
        assert select.call_count == 1
    restored = application(tmp_path)
    assert restored.lighting.state.pending == 0
    with mock.patch.object(restored.lyte, 'select_animation') as select:
        assert not act(restored, 'accept').ok
        assert act(restored, 'accept', confirmation='resolve').ok
        assert restored.lighting.state.current == 0
        select.assert_not_called()


@pytest.mark.parametrize('resolution', ['cancel', 'retry'])
def test_explicit_resolution_after_rejection(tmp_path: Path, resolution: str) -> None:
    app = application(tmp_path)
    prepare(app)
    with mock.patch.object(
        app.lyte,
        'select_animation',
        return_value=models.ActionResult(ok=False, message='unconfirmed'),
    ):
        assert not act(app, 'go').ok
    assert act(app, resolution, confirmation='resolve').ok
    assert app.lighting.state.pending is None
    assert app.lighting.state.current == (0 if resolution == 'retry' else -1)
    assert app.lyte is not None
    assert app.lyte.status().active_animation == (
        'circle' if resolution == 'retry' else 'idle'
    )


def test_storage_failure_prevents_selection_and_post_send_failure_keeps_pending(
    tmp_path: Path,
) -> None:
    app = application(tmp_path)
    prepare(app)
    with mock.patch.object(Path, 'write_text', side_effect=OSError('disk full')):
        with mock.patch.object(app.lyte, 'select_animation') as select:
            assert not act(app, 'go').ok
            select.assert_not_called()
    original = Path.write_text
    calls = 0

    def write(path: Path, data: str) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('disk full after send')
        return original(path, data)

    with mock.patch.object(Path, 'write_text', write):
        assert not act(app, 'go').ok
    assert app.lighting.state.pending == 0
    assert lighting.LightingController(tmp_path / 'lighting.json').state.pending == 0
    assert app.lyte is not None
    assert app.lyte.status().active_animation == 'circle'


def test_performance_lock_blocks_edits_but_allows_cues(tmp_path: Path) -> None:
    app = application(tmp_path)
    prepare(app)
    app.performance_lock.set(True)
    assert not act(app, 'save', cues='[]').ok
    assert act(app, 'go').ok
    assert act(app, 'go').ok
    assert act(app, 'back').ok
    app.lyte = None
    assert not act(app, 'go').ok


def test_selection_uses_existing_rpc_and_checks_acknowledgement() -> None:
    with mock.patch('showco.runtime.lyte.rpc.Client') as factory:
        client = factory.return_value
        client.call.return_value = {'state': 'queued', 'name': 'circle'}
        assert LyteClient(enabled=True).select_animation('circle').ok
        client.call.assert_called_once_with('select_animation', name='circle')
        client.call.return_value = {'state': 'queued', 'name': 'square'}
        assert not LyteClient(enabled=True).select_animation('circle').ok
        client.call.side_effect = TimeoutError('lost')
        with pytest.raises(TimeoutError):
            LyteClient(enabled=True).select_animation('circle')


def test_live_state_distinguishes_queued_and_overridden_looks() -> None:
    with mock.patch('showco.runtime.lyte.rpc.Client') as factory:
        factory.return_value.call.return_value = dict(
            animations=['idle', 'circle'],
            active_animation='idle',
            queued_animation='circle',
            blackout=True,
        )
        status = LyteClient(enabled=True).status()
    assert status.animations == ['idle', 'circle']
    assert status.active_animation == 'idle'
    assert status.queued_animation == 'circle'
    assert status.blackout


def test_lighting_page_has_manual_controls_and_editor() -> None:
    page = views.configured_page(
        gui_schema.current_gui().page('lighting'),
        models.ShowStatus(
            recs=models.RecsStatus(
                service=models.ServiceStatus(name='recs', state='connected')
            ),
            streamo=models.StreamoStatus(
                service=models.ServiceStatus(name='streamo', state='disabled')
            ),
        ),
    )
    assert 'data-lighting-action="lighting-go"' in page
    assert 'data-lighting-action="lighting-back"' in page
    assert 'id="lighting-editor"' in page
    assert 'lighting.js' not in page  # Scripts are inlined for deployment.
    assert 'function pollLighting()' in page
    assert 'function pollWorkflows()' not in page


def test_browser_lighting_controls_and_reconnect(tmp_path: Path) -> None:
    app = application(tmp_path)
    prepare(app)
    with mock.patch('showco.runtime.workflows.refresh_soundcheck'):
        payload = workflows.status(app)
    subprocess.run(
        ['node', 'test/browser_lighting.cjs'],
        input=payload.model_dump_json(),
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
