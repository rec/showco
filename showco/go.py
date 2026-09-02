from __future__ import annotations

import sys

import tyro

from . import machine_role, update
from .provision import provision, remote, script, state


def main(argv: list[str] | None = None) -> int:
    machine_role.require_provisioning_machine("showco go")
    options = tyro.cli(
        provision.ProvisionOptions,
        args=argv,
        description=(
            "Provision a target when its configuration changed, otherwise update it"
        ),
    )
    return run(options)


def run(options: provision.ProvisionOptions) -> int:
    provision_config = provision.resolved_config(options)
    if options.system:
        print(f"System update requested: provisioning {provision_config.ssh_target}...")
        return provision.run(options, provision_config=provision_config)

    fingerprint = state.provisioning_fingerprint(provision_config, script.REMOTE_SCRIPT)
    applied = remote.applied_provisioning_fingerprint(provision_config)
    if applied != fingerprint:
        if applied is None:
            print(
                f"No applied provisioning state: provisioning "
                f"{provision_config.ssh_target}..."
            )
        else:
            print(
                f"Configuration differs: provisioning {provision_config.ssh_target}..."
            )
        return provision.run(options, provision_config=provision_config)

    print(f"Configuration matches: updating {provision_config.ssh_target}...")
    return update.update_from_provisioning_machine(
        update.REPOSITORY_NAMES,
        host=options.host,
        root=options.root,
        target_config=provision_config,
        output=sys.stdout,
    )
