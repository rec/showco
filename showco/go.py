from __future__ import annotations

import sys

import tyro

from . import machine_role, update
from .provision import provision, remote, script, state


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        provision.GoOptions,
        args=argv,
        description="Provision or update the show-control target",
    )
    result = run(options)
    if result == 0:
        print("Successfully completed")
    return result


def run(options: provision.GoOptions) -> int:
    selected = update.selected_repositories(options.repositories or [])
    if (
        options.target_machine
        or machine_role.machine_role() == machine_role.TARGET_ROLE
    ):
        if options.system or options.remote:
            sys.exit(
                "ERROR: --system and --remote are unavailable on the target machine"
            )
        return update.update_target(
            selected,
            root=options.root,
            clear_settings=options.clear_settings,
        )

    machine_role.require_provisioning_machine("showco go")
    update_requested = (
        options.repositories is not None or options.autosquash is not None
    )
    if options.system and (update_requested or options.remote):
        sys.exit("ERROR: --system cannot be combined with update options")
    provision_config = provision.resolved_config(options)
    if options.remote:
        return update.update_remote_target(
            selected,
            host=options.host,
            root=options.root,
            target_config=provision_config,
            output=sys.stdout,
            clear_settings=options.clear_settings,
        )
    if update_requested:
        return update.update_from_provisioning_machine(
            selected,
            host=options.host,
            root=options.root,
            target_config=provision_config,
            output=sys.stdout,
            autosquash=options.autosquash or 50,
            clear_settings=options.clear_settings,
        )
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
        clear_settings=options.clear_settings,
    )
