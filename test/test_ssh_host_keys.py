from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from provision_helpers import make_config, networks, values

from showco.provision import remote, ssh


@pytest.mark.parametrize('port', [22, 2200])
@pytest.mark.parametrize('answer', ['yes', 'no', None])
def test_changed_key_replaces_only_the_approved_host(
    tmp_path: Path, port: int, answer: str | None
) -> None:
    config = make_config(values(networks=networks(x18=False)), port=port)
    key = base64.b64encode(b'new public host key').decode()
    fingerprint = 'SHA256:' + base64.b64encode(
        hashlib.sha256(b'new public host key').digest()
    ).decode().rstrip('=')
    changed = subprocess.CompletedProcess(
        ['ssh'], 255, '', f'REMOTE HOST IDENTIFICATION HAS CHANGED!\n{fingerprint}'
    )
    known_hosts = tmp_path / '.ssh' / 'known_hosts'
    known_hosts.parent.mkdir()
    original = 'another-host ssh-ed25519 unrelated-key\n'
    known_hosts.write_text(original)
    scanned = subprocess.CompletedProcess(
        ['ssh-keyscan'], 0, f'recs-stage.local ssh-ed25519 {key}\n', ''
    )
    removed = subprocess.CompletedProcess(['ssh-keygen'], 0, '', '')
    with (
        mock.patch('showco.provision.ssh.Path.home', return_value=tmp_path),
        mock.patch(
            'reccy.runtime.subprocess.run', side_effect=[scanned, removed]
        ) as run,
        mock.patch(
            'builtins.input',
            return_value=answer,
            side_effect=EOFError if answer is None else None,
        ),
        mock.patch('builtins.print') as output,
    ):
        if answer == 'yes':
            ssh.approve_changed_host_key(config, changed)
            host = 'recs-stage.local' if port == 22 else '[recs-stage.local]:2200'
            assert run.call_args_list[1].args[0] == [
                'ssh-keygen',
                '-R',
                host,
                '-f',
                str(known_hosts),
            ]
            assert known_hosts.read_text() == original + f'\n{host} ssh-ed25519 {key}\n'
        else:
            with pytest.raises(SystemExit, match='declined'):
                ssh.approve_changed_host_key(config, changed)
            assert run.call_count == 1
            assert known_hosts.read_text() == original
        output.assert_any_call(f'New host key: {fingerprint}')


def test_scan_must_match_the_key_reported_by_ssh() -> None:
    config = make_config(values(networks=networks(x18=False)))
    changed = subprocess.CompletedProcess(
        ['ssh'], 255, '', 'REMOTE HOST IDENTIFICATION HAS CHANGED! SHA256:unexpected'
    )
    scanned = subprocess.CompletedProcess(
        ['ssh-keyscan'], 0, 'recs-stage.local ssh-ed25519 cHVibGlj\n', ''
    )
    with (
        mock.patch('reccy.runtime.subprocess.run', return_value=scanned) as run,
        mock.patch('builtins.input') as prompt,
        pytest.raises(SystemExit, match='does not match'),
    ):
        ssh.approve_changed_host_key(config, changed)
    assert run.call_count == 1
    prompt.assert_not_called()


def test_provisioning_state_check_continues_after_key_approval() -> None:
    config = make_config(values(networks=networks(x18=False)))
    changed = subprocess.CompletedProcess(
        ['ssh'], 255, '', 'REMOTE HOST IDENTIFICATION HAS CHANGED!'
    )
    connected = subprocess.CompletedProcess(['ssh'], 42, '', '')
    with (
        mock.patch('reccy.runtime.subprocess.run', side_effect=[changed, connected]),
        mock.patch('showco.provision.ssh.approve_changed_host_key') as approve,
    ):
        assert remote.applied_provisioning_fingerprint(config) is None
    approve.assert_called_once_with(config, changed)
