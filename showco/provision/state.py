from __future__ import annotations

import hashlib
import json

from . import config


def provisioning_fingerprint(
    provision_config: config.Config, remote_script: str
) -> str:
    value = {
        "config": provision_config.model_dump(mode="json"),
        "remote_script": remote_script,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
