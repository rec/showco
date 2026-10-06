from __future__ import annotations

from io import StringIO
from unittest.mock import Mock

import pytest

from showco.deployment import panel
from showco.provision import config


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> Mock:
    process = Mock()
    process.stdout = StringIO('showco-panel-ready\n')
    process.stdin = StringIO()
    process.wait.return_value = 0
    process.poll.return_value = 0
    factory = Mock(return_value=process)
    monkeypatch.setattr(panel.subprocess, 'Popen', factory)
    monkeypatch.setattr(panel.machine_role, 'require_provisioning_machine', Mock())
    monkeypatch.setattr(panel.webbrowser, 'open', Mock(return_value=True))
    return factory


def test_panel_opens_loopback_tunnel_using_configured_target(connection: Mock) -> None:
    configuration = target_config()
    configuration = configuration.model_copy(
        update={
            'network': configuration.network.model_copy(
                update={'ssh_port': 2222, 'web_port': 18080}
            )
        }
    )

    assert panel.open_panel(panel.PanelOptions(), target_config=configuration) == 0

    command = connection.call_args.args[0]
    assert command[command.index('-L') + 1] == '127.0.0.1:18080:127.0.0.1:18080'
    assert command[command.index('-p') + 1] == '2222'
    assert command[-2] == 'tom@bertrand.local'
    assert 'ExitOnForwardFailure=yes' in command
    assert 'BatchMode=yes' in command
    panel.webbrowser.open.assert_called_once_with('http://127.0.0.1:18080')
    assert connection.return_value.stdin.closed
    assert connection.return_value.stdout.closed


def test_panel_can_use_another_local_port(connection: Mock) -> None:
    assert (
        panel.open_panel(panel.PanelOptions(port=17353), target_config=target_config())
        == 0
    )
    command = connection.call_args.args[0]
    assert command[command.index('-L') + 1] == '127.0.0.1:17353:127.0.0.1:17352'


def test_failed_tunnel_reports_ssh_error_without_opening_browser(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    process = connection.return_value
    process.stdout = StringIO()
    process.wait.return_value = 255
    process.poll.return_value = 255

    def fail(*args: object, **kwargs: object) -> Mock:
        kwargs['stderr'].write('bind: Address already in use\n')
        return process

    connection.side_effect = fail

    assert panel.open_panel(panel.PanelOptions(), target_config=target_config()) == 255
    assert 'Address already in use' in capsys.readouterr().err
    panel.webbrowser.open.assert_not_called()


def test_interruption_closes_tunnel(connection: Mock) -> None:
    process = connection.return_value
    process.poll.return_value = None
    process.wait.side_effect = [KeyboardInterrupt, 0]

    with pytest.raises(KeyboardInterrupt):
        panel.open_panel(panel.PanelOptions(), target_config=target_config())

    assert process.stdin.closed
    process.terminate.assert_called_once()
    assert process.wait.call_args.kwargs == {'timeout': 5}


def test_browser_unavailable_leaves_url_for_manual_access(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    panel.webbrowser.open.return_value = False

    assert panel.open_panel(panel.PanelOptions(), target_config=target_config()) == 0
    assert 'Open the URL above manually' in capsys.readouterr().out


def target_config() -> config.Config:
    return config.config_from_values(
        {
            'network': {'host': 'bertrand.local', 'user': 'tom'},
            'paths': {'root': '/srv/show-projects'},
            'networks': {'internal': {'subnet': '10.0.0.0/24', 'wifi': {}}},
            'git': {
                n: {'url': f'git@github.com:rec/{n}'}
                for n in ('reccy', 'recs', 'streamo', 'showco', 'lyte')
            },
        }
    )
