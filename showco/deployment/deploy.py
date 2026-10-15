from __future__ import annotations

import sys

import tyro

from ..provision import media, provision, remote, script, ssh, state
from . import card, local_update, machine_role, target_update, update


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        provision.DeployOptions,
        args=argv,
        description='Provision or update the show-control target',
    )
    result = run(options)
    if result == 0:
        print('Successfully completed')
    return result


def run(options: provision.DeployOptions) -> int:
    selected = update.selected_repositories(options.repositories or [])
    if options.push and options.sync:
        sys.exit('ERROR: --push and --sync cannot be combined')
    if (
        options.movie
        or options.photos
        or options.audio
        or options.setup
        or options.teardown
    ) and (options.push or options.sync or options.remote or options.target_machine):
        sys.exit(
            'ERROR: media paths require provisioning; do not combine them with '
            '--push, --sync, --remote, or --target-machine'
        )
    if (
        options.target_machine
        or machine_role.machine_role() == machine_role.TARGET_ROLE
    ):
        if (
            options.movie
            or options.photos
            or options.audio
            or options.setup
            or options.teardown
        ):
            sys.exit(
                'ERROR: media provisioning flags must run on the development machine; '
                'on the Pi, showco run accepts existing audio paths'
            )
        if options.upgrade or options.remote or options.push or options.sync:
            sys.exit(
                'ERROR: --upgrade, --remote, --push, and --sync are unavailable '
                'on the target machine'
            )
        return target_update.update_target(
            selected,
            root=options.root,
            clear_settings=options.clear_settings,
        )

    machine_role.require_provisioning_machine('showco deploy')
    update_requested = (
        options.repositories is not None or options.autosquash is not None
    ) and not (
        options.movie
        or options.photos
        or options.audio
        or options.setup
        or options.teardown
    )
    if options.upgrade and (
        update_requested or options.remote or options.push or options.sync
    ):
        sys.exit('ERROR: --upgrade cannot be combined with update options')
    if options.remote and (options.push or options.sync):
        sys.exit('ERROR: --remote cannot be combined with --push or --sync')
    if options.push or options.sync:
        local_root = provision.local_checkout_directory()
        if not local_update.prepare_local_repositories(
            selected,
            local_root,
            update.run_command_with_timeout,
            sys.stdout,
            autosquash=options.autosquash if options.autosquash is not None else 50,
        ):
            return 1
        if options.sync and not local_update.refresh_local_dependencies(
            selected,
            local_root,
            update.run_command_with_timeout,
            sys.stdout,
        ):
            return 1
        return 0
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
        return local_update.update_from_provisioning_machine(
            selected,
            host=options.host,
            root=options.root,
            target_config=provision_config,
            output=sys.stdout,
            autosquash=options.autosquash if options.autosquash is not None else 50,
            clear_settings=options.clear_settings,
        )
    media.save_sources(options.config_path, provision_config.media)
    cards = card.attached_imager_cards()
    if len(cards) > 1:
        sys.exit(
            'ERROR: multiple Imager-written Raspberry Pi cards found: '
            + ', '.join(str(c) for c in cards)
            + '; disconnect all but the intended card and try again'
        )
    if cards:
        previous_boot_id = ssh.ssh_boot_id(provision_config)
        card.prepare_selected_card(cards[0], provision_config.network.user)
        print(
            'Insert the prepared card into the Pi and turn it on. '
            f'Waiting up to five minutes for {provision_config.ssh_target}; '
            'press Ctrl-C to cancel.'
        )
        if previous_boot_id is not None:
            ssh.wait_for_rebooted_ssh(provision_config, previous_boot_id)
        else:
            ssh.wait_for_ssh(provision_config, timeout_seconds=ssh.REBOOT_WAIT_SECONDS)
        print('Waiting for Raspberry Pi first-boot setup to finish...')
        ssh.run_ssh(
            provision_config,
            'cloud-init status --wait --long',
            timeout_seconds=ssh.REBOOT_WAIT_SECONDS,
        )
        return provision.run(options, provision_config=provision_config)

    if options.upgrade:
        print(
            f'Package upgrade requested: provisioning {provision_config.ssh_target}...'
        )
        return provision.run(options, provision_config=provision_config)

    fingerprint = state.provisioning_fingerprint(provision_config, script.REMOTE_SCRIPT)
    applied = remote.applied_provisioning_fingerprint(provision_config)
    if applied != fingerprint:
        if applied is None:
            print(
                f'No applied provisioning state: provisioning '
                f'{provision_config.ssh_target}...'
            )
        else:
            print(
                f'Configuration differs: provisioning {provision_config.ssh_target}...'
            )
        return provision.run(options, provision_config=provision_config)

    media.synchronize(provision_config)
    media.configure_stream_media(provision_config)
    print(f'Configuration matches: updating {provision_config.ssh_target}...')
    return local_update.update_from_provisioning_machine(
        update.REPOSITORY_NAMES,
        host=options.host,
        root=options.root,
        target_config=provision_config,
        output=sys.stdout,
        clear_settings=options.clear_settings,
    )
