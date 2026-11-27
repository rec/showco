from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from . import config


def provisioning_fingerprint_path(provision_config: config.Config) -> Path:
    return (
        Path('/home')
        / provision_config.network.user
        / '.local/state/showco/provisioning-fingerprint'
    )


def lock_configuration(provision_config: config.Config, command: str) -> str:
    directory = provisioning_fingerprint_path(provision_config).parent
    lock = shlex.quote(str(directory / 'provision.lock'))
    return (
        f'mkdir -p -- {shlex.quote(str(directory))} && exec 9>{lock} || exit $?; '
        'flock -n 9 || { '
        "printf '%s\\n' 'ERROR: another showCo provision is applying configuration; "
        "retry shortly.' >&2; exit 1; }; " + command
    )


def provisioning_fingerprint(
    provision_config: config.Config, remote_script: str, *, prepared: bool = False
) -> str:
    exclude = {'media', 'movie', 'audio', 'setup', 'teardown', 'verify_media'}
    value = {
        'config': provision_config.model_dump(
            mode='json', exclude=exclude if prepared else None
        ),
        'remote_script': remote_script,
    }
    if prepared:
        value['phase'] = 'prepare'
    elif any(provision_config.media.sources.values()):
        package = provision_config.media_package
        value['media'] = [package.version, package.metadata_version]
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()
