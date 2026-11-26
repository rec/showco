from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TextIO

from ..provision import config, verify
from ..runtime import gui_schema, services
from . import update


def update_target(
    selected: list[str],
    *,
    root: Path | None = None,
    run_command: update.RunCommand | None = None,
    output: TextIO = sys.stdout,
    clear_settings: bool = False,
) -> int:
    provision_config = update.provisioning_config()
    root = root or provision_config.paths.root
    run_command = run_command or update.run_command_with_timeout
    programs = update.programs_for_repositories(
        update.expand_repository_selection(selected),
        root,
        provision_config.stream.enabled,
        provision_config.lyte.enabled,
    )
    if not update.check_main_branches(programs, run_command, output):
        return 1
    clean = [update.clean_worktree_step(p, run_command) for p in programs]
    if not all(r.ok for r in clean):
        update.report_failures(clean, output)
        return 1

    previous: dict[str, str] = {}
    targets: dict[str, str] = {}
    for program in programs:
        for label, revision_name, destination in [
            ('previous revision', 'HEAD', previous),
            ('target revision', '@{upstream}', targets),
        ]:
            if destination is targets:
                fetched = update.run_step(
                    program.name,
                    'fetch',
                    ['git', '-C', str(program.directory), 'fetch'],
                    run_command,
                )
                if not fetched.ok:
                    update.report_failure(fetched, output)
                    return 1
            result = update.run_step(
                program.name,
                label,
                [
                    'git',
                    '-C',
                    str(program.directory),
                    'rev-parse',
                    '--verify',
                    revision_name,
                ],
                run_command,
            )
            if not result.ok or not result.output.strip():
                update.report_failure(result, output)
                if result.ok:
                    print(f'{program.name}: empty {label}', file=output)
                return 1
            destination[program.name] = result.output.strip()

    if 'showco' in targets:
        candidate = update.run_step(
            'showco',
            'validate GUI',
            [
                'git',
                '-C',
                str(root / 'showco'),
                'show',
                f'{targets["showco"]}:{provision_config.gui_path.as_posix()}',
            ],
            run_command,
        )
        if not candidate.ok:
            update.report_failure(candidate, output)
            return 1
        try:
            gui_schema.parse_gui(candidate.output, str(provision_config.gui_path))
        except ValueError as error:
            print(error, file=output)
            return 1

    names = update.selected_service_names(programs)
    update_publisher = 'streamo' in names
    restart_names = [n for n in names if n not in {'streamo', 'showco'}]
    if update_publisher or 'showco' in names:
        # Stop the controller before taking streamO down. Its actions can start
        # the publisher, including during an update limited to streamO's code.
        restart_names.append('showco')
    service_state = active_service_enablement(restart_names, run_command, output)
    if service_state is None:
        return 1
    active_services, failed_services = service_state
    for name in failed_services:
        detail = ' Recording is unavailable.' if name == 'recs' else ''
        print(
            f'{name}: service was already failed and will be left stopped.{detail}',
            file=output,
        )
    active_names = list(active_services)
    refresh = any(p.name == 'reccy' for p in programs)
    settings_path = (
        Path('/home') / provision_config.network.user / '.config/recs/settings.json'
    )
    restore_settings = clear_settings and 'recs' in names
    saved_settings: bytes | None = None
    if restore_settings:
        try:
            saved_settings = settings_path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError as error:
            print(f'Cannot back up recs settings: {error}', file=output)
            return 1
    try:
        stopped = stop_services(active_names, run_command)
        if 'showco' in restart_names and all(r.ok for r in stopped):
            stopped.append(
                update.run_step(
                    'showco',
                    'clear Twitch recovery authorization',
                    [
                        'sh',
                        '-c',
                        'state=/run/user/$(id -u)/showco-twitch.json; '
                        'if [ -e "$state" ]; then unlink "$state"; fi',
                    ],
                    run_command,
                )
            )
        if update_publisher and all(r.ok for r in stopped):
            stopped.append(stop_publisher_for_update(run_command))
    except (OSError, KeyboardInterrupt) as error:
        print(f'Service shutdown interrupted: {error or "Interrupted"}', file=output)
        update.report_failures(
            [update.run_service_step(n, 'start', run_command) for n in active_names],
            output,
        )
        return 1
    if not all(r.ok for r in stopped):
        update.report_failures(stopped, output)
        update.report_failures(
            [update.run_service_step(n, 'start', run_command) for n in active_names],
            output,
        )
        return 1

    results: list[update.StepResult] = []
    try:
        if restore_settings:
            results.append(
                update.clear_recs_settings_step(
                    provision_config.network.user, run_command
                )
            )
        if all(r.ok for r in results):
            results.extend(install_revisions(programs, targets, run_command))
        if update_publisher and all(r.ok for r in results):
            results.append(
                update.install_streamo_service(
                    root, provision_config.network.user, run_command
                )
            )
            if results[-1].ok:
                results.append(
                    update.run_step(
                        'streamo',
                        'verify manual service',
                        ['sh', '-c', verify.streamo_manual_service_command()],
                        run_command,
                    )
                )
        if all(r.ok for r in results):
            results.extend(
                restart_services(
                    active_services, refresh, root, provision_config, run_command
                )
            )
        if all(r.ok for r in results):
            results.extend(
                check_updated_services(
                    active_names, root, provision_config, run_command
                )
            )
        if all(r.ok for r in results):
            if failed_services:
                print(
                    'Update installed. Previously failed services were left stopped.',
                    file=output,
                )
            return int(bool(failed_services))

    except (OSError, KeyboardInterrupt) as error:
        results.append(
            update.StepResult(
                program='target',
                step='deployment interrupted',
                command=[],
                returncode=1,
                output=str(error) or 'Interrupted',
            )
        )

    update.report_failures(results, output)
    print('Update failed; restoring previous revisions and environments.', file=output)
    stopped = stop_services(active_names, run_command)
    if not all(r.ok for r in stopped):
        update.report_failures(stopped, output)
        print('Rollback blocked: could not stop affected services.', file=output)
        return 1
    restored = install_revisions(programs, previous, run_command, restore=True)
    update.report_failures(restored, output)
    if restore_settings:
        try:
            if saved_settings is None:
                settings_path.unlink(missing_ok=True)
            else:
                temporary_path: Path | None = None
                try:
                    with NamedTemporaryFile(
                        dir=settings_path.parent, prefix='.settings-', delete=False
                    ) as temporary:
                        temporary_path = Path(temporary.name)
                        temporary.write(saved_settings)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    temporary_path.replace(settings_path)
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
        except OSError as error:
            print(
                f'Rollback incomplete: cannot restore recs settings: {error}',
                file=output,
            )
            return 1
    if not all(r.ok for r in restored):
        print('Rollback incomplete; affected services remain stopped.', file=output)
        return 1
    restarted = restart_services(
        active_services, refresh, root, provision_config, run_command
    )
    restarted.extend(
        check_updated_services(active_names, root, provision_config, run_command)
    )
    update.report_failures(restarted, output)
    if all(r.ok for r in restarted):
        print('Previous versions restored and services restarted.', file=output)
    else:
        print('Previous versions restored, but service recovery failed.', file=output)
    return 1


def active_service_enablement(
    names: list[str], run_command: update.RunCommand, output: TextIO
) -> tuple[dict[str, str], list[str]] | None:
    active = {}
    failed = []
    for name in names:
        unit = services.SERVICES[name].systemd_unit
        read = update.run_step(
            name,
            'read service state',
            [
                'systemctl',
                '--user',
                'show',
                unit,
                '--property=ActiveState',
                '--property=UnitFileState',
            ],
            run_command,
        )
        if not read.ok:
            update.report_failure(read, output)
            return None
        fields = dict(
            line.split('=', 1) for line in read.output.splitlines() if '=' in line
        )
        state = fields.get('ActiveState')
        enablement = fields.get('UnitFileState', '')
        if state == 'failed':
            failed.append(name)
            continue
        if state == 'inactive':
            continue
        if (
            state not in {'active', 'reloading'}
            or not enablement
            or enablement.startswith('masked')
        ):
            print(
                f'{name}: cannot preserve service state '
                f'(active={state or "unknown"}, enabled={enablement or "unknown"}). '
                'Resolve the service state before updating.',
                file=output,
            )
            return None
        active[name] = enablement
    return active, failed


def stop_publisher_for_update(run_command: update.RunCommand) -> update.StepResult:
    unit = 'streamo.service'
    state = update.run_step(
        'streamo',
        'read publisher unit',
        ['systemctl', '--user', 'show', unit, '--property=LoadState', '--value'],
        run_command,
    )
    if not state.ok or state.output.strip() == 'not-found':
        return state
    if state.output.strip() == 'masked':
        return update.run_service_step('streamo', 'stop', run_command)
    return update.run_step(
        'streamo',
        'disable and stop publisher',
        ['systemctl', '--user', 'disable', unit, '--now'],
        run_command,
    )


def restart_services(
    active: dict[str, str],
    refresh: bool,
    root: Path,
    provision_config: config.Config,
    run_command: update.RunCommand,
) -> list[update.StepResult]:
    results = []
    for name, enablement in active.items():
        reinstall = refresh and enablement in {'enabled', 'disabled', 'enabled-runtime'}
        try:
            results.append(
                update.start_or_refresh_service_step(
                    name, reinstall, root, provision_config, run_command
                )
            )
        finally:
            if reinstall and enablement != 'enabled':
                unit = services.SERVICES[name].systemd_unit
                restored = update.run_step(
                    name,
                    'restore service enablement',
                    ['systemctl', '--user', 'disable', unit],
                    run_command,
                )
                results.append(restored)
                if restored.ok and enablement == 'enabled-runtime':
                    results.append(
                        update.run_step(
                            name,
                            'restore runtime enablement',
                            ['systemctl', '--user', 'enable', unit, '--runtime'],
                            run_command,
                        )
                    )
    return results


def install_revisions(
    programs: list[update.Program],
    revisions: dict[str, str],
    run_command: update.RunCommand,
    *,
    restore: bool = False,
) -> list[update.StepResult]:
    results = []
    for program in programs:
        result = update.run_step(
            program.name,
            'restore revision' if restore else 'install revision',
            [
                'git',
                '-C',
                str(program.directory),
                'reset',
                '--hard',
                revisions[program.name],
            ],
            run_command,
        )
        results.append(result)
        if not result.ok and not restore:
            return results
    if not all(r.ok for r in results):
        return results
    for program in programs:
        result = update.run_step(
            program.name,
            'restore dependencies' if restore else 'sync dependencies',
            ['uv', 'sync', '--locked', '--directory', str(program.directory)],
            run_command,
        )
        results.append(result)
        if not result.ok and not restore:
            return results
    return results


def stop_services(
    names: list[str], run_command: update.RunCommand
) -> list[update.StepResult]:
    results = []
    for name in reversed(names):
        result = update.run_service_step(name, 'stop', run_command)
        if not result.ok and result.output.rstrip().endswith(
            f'Unit {name}.service not loaded.'
        ):
            result = result.model_copy(update={'returncode': 0, 'output': 'not loaded'})
        results.append(result)
    return results


def check_updated_services(
    names: list[str],
    root: Path,
    provision_config: config.Config,
    run_command: update.RunCommand,
) -> list[update.StepResult]:
    results = []
    if 'showco' in names:
        results.append(
            update.showco_revision_step(
                root, provision_config.network.web_port, run_command
            )
        )
    if 'recs' in names:
        results.append(update.recs_status_changes_step(run_command))
    if 'lyte' in names and provision_config.lyte.enabled:
        results.append(
            update.run_step(
                'lyte',
                'service health',
                ['systemctl', '--user', 'is-active', '--quiet', 'lyte.service'],
                run_command,
            )
        )
    return results
