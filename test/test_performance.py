from html.parser import HTMLParser
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import pytest

from showco.runtime import gui_schema, models, performance, rehearsal, views
from showco.runtime.app import RECORDING_ACTIONS, ShowcoApp


def app(directory: Path) -> ShowcoApp:
    return ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        state_directory=directory,
    )


@pytest.mark.parametrize('action', sorted(performance.PROTECTED_ACTIONS))
def test_lock_blocks_protected_action_before_dispatch(
    tmp_path: Path, action: str
) -> None:
    application = app(tmp_path)
    assert application.run_action({'action': 'performance-lock'}).ok
    with mock.patch.object(application.recs, 'action') as recorder:
        result = application.run_action({'action': action})
    assert not result.ok
    assert 'Performance lock blocks' in result.message
    recorder.assert_not_called()


@pytest.mark.parametrize(
    'action',
    ['streamo-stop', 'streamo-restart', 'music-close-cancel', 'music-close-finish'],
)
def test_lock_prevents_stream_and_closing_recovery_side_effects(
    tmp_path: Path, action: str
) -> None:
    application = app(tmp_path)
    application.streamo = mock.Mock()
    application.streamo_restart = mock.Mock()
    application.music = mock.Mock()
    application.performance_lock.set(True)
    result = application.run_action(
        {'action': action, 'confirmation': 'broadcast-stopped'}
    )
    assert not result.ok
    assert 'Performance lock blocks' in result.message
    application.streamo.action.assert_not_called()
    application.streamo_restart.assert_not_called()
    application.music.recover_closing.assert_not_called()


def test_lock_survives_restart_and_unlock_does_not_replay_actions(
    tmp_path: Path,
) -> None:
    first = app(tmp_path)
    first.run_action({'action': 'performance-lock'})
    second = app(tmp_path)
    assert second.status().performance_locked
    assert not second.run_action({'action': 'recs-new-session'}).ok
    assert not second.run_action({'action': 'performance-unlock'}).ok
    with mock.patch.object(second.recs, 'action') as recorder:
        assert second.run_action(
            {'action': 'performance-unlock', 'confirmation': 'unlock'}
        ).ok
    recorder.assert_not_called()
    assert not app(tmp_path).status().performance_locked


@pytest.mark.parametrize(
    'action', ['recs-marker', 'recs-pause-recording', 'recs-resume-recording']
)
def test_lock_allows_normal_performance_controls(tmp_path: Path, action: str) -> None:
    application = app(tmp_path)
    application.run_action({'action': 'performance-lock'})
    assert application.run_action({'action': action, 'label': 'moment'}).ok


def test_failed_lock_write_does_not_report_success(tmp_path: Path) -> None:
    application = app(tmp_path)
    with mock.patch.object(Path, 'write_text', side_effect=OSError('full disk')):
        result = application.run_action({'action': 'performance-lock'})
    assert not result.ok
    assert not application.performance_lock.locked


@pytest.mark.parametrize('content', ['broken', '{}'])
def test_corrupt_lock_blocks_actions_until_explicit_successful_unlock(
    tmp_path: Path,
    content: str,
) -> None:
    (tmp_path / 'performance.json').write_text(content)
    application = app(tmp_path)
    assert application.status().performance_locked
    assert 'Cannot read performance lock' in application.status().monitoring_error
    assert not application.run_action({'action': 'cable-test'}).ok
    assert application.run_action(
        {'action': 'performance-unlock', 'confirmation': 'unlock'}
    ).ok
    assert not app(tmp_path).performance_lock.locked


def test_performance_controls_and_protection_are_present_on_all_pages() -> None:
    page = views.configured_page(
        gui_schema.current_gui().page('performance'),
        models.ShowStatus(
            recs=models.RecsStatus(
                service=models.ServiceStatus(name='recs', state='connected')
            ),
            streamo=models.StreamoStatus(
                service=models.ServiceStatus(name='streamo', state='disabled')
            ),
        ),
    )
    assert 'data-performance-action="recs-marker"' in page
    assert 'id="performance-disk"' in page
    assert 'id="input-pins"' in page
    for content in [page, views.page('Actions', ''), views.page('Errors', '')]:
        assert 'id="performance-lock-state"' in content
        assert 'id="fault-banner"' in content
        assert 'id="connection-status"' in content


@pytest.mark.parametrize('action', sorted(RECORDING_ACTIONS))
def test_recording_controls_are_unavailable_until_show_transition_finishes(
    tmp_path: Path, action: str
) -> None:
    application = app(tmp_path)
    music = mock.Mock()
    application.music = music
    music.status.return_value = models.MusicStatus(mode='record')
    entered = Event()
    release = Event()

    def record() -> models.ActionResult:
        music.status.return_value = models.MusicStatus(mode='transitioning')
        entered.set()
        assert release.wait(timeout=5)
        music.status.return_value = models.MusicStatus(mode='record')
        return models.ActionResult(ok=True, message='Recording resumed')

    music.record.side_effect = record
    worker = Thread(target=application.run_action, args=({'action': 'music-record'},))
    with mock.patch.object(application.recs, 'action') as recorder:
        worker.start()
        try:
            assert entered.wait(timeout=5)
            assert not application.status().recording_controls_available
            outcome = application.run_action({'action': action})
            assert not outcome.ok
            recorder.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
        assert application.status().recording_controls_available
        recorder.return_value = models.ActionResult(ok=True, message='ok')
        if action.startswith('recs-'):
            assert application.run_action({'action': action}).ok
            recorder.assert_called_once()


@pytest.mark.parametrize('action', sorted(RECORDING_ACTIONS))
def test_closing_disables_recording_controls_between_actions(
    tmp_path: Path, action: str
) -> None:
    application = app(tmp_path)
    application.music = mock.Mock()
    application.music.status.return_value = models.MusicStatus(mode='closing')
    with mock.patch.object(application.recs, 'action') as recorder:
        assert not application.status().recording_controls_available
        outcome = application.run_action({'action': action})
        assert not outcome.ok
        assert 'show transition is in progress' in outcome.message
        recorder.assert_not_called()


@pytest.mark.parametrize('page_name', ['actions', 'performance'])
def test_initial_page_disables_unavailable_recording_controls(page_name: str) -> None:
    class Buttons(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.buttons: list[dict[str, str | None]] = []
            self.action: str | None = None

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            values = dict(attrs)
            if tag == 'form':
                self.action = values.get('data-action')
            if tag == 'button':
                values['action'] = values.get('data-performance-action') or self.action
                self.buttons.append(values)

    show = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected')
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
        recording_controls_available=False,
    )
    parser = Buttons()
    parser.feed(views.configured_page(gui_schema.current_gui().page(page_name), show))
    buttons = [
        b
        for b in parser.buttons
        if b['action'] in {'recs-pause-recording', 'recs-resume-recording'}
    ]
    assert len(buttons) == 2
    assert all('disabled' in b for b in buttons)
