from pathlib import Path
from unittest import mock

import pytest

from showco.runtime import performance, rehearsal, views
from showco.runtime.server import ShowcoApp


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
    page = views.performance_page()
    assert 'data-performance-action="recs-marker"' in page
    assert 'id="performance-disk"' in page
    assert 'id="input-pins"' in page
    for content in [page, views.page('Actions', ''), views.errors_page([])]:
        assert 'id="performance-lock-state"' in content
        assert 'id="fault-banner"' in content
        assert 'id="connection-status"' in content
