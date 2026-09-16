from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from ..provision import config
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

    names = update.selected_service_names(programs)
    names = [n for n in names if n != 'showco'] + (
        ['showco'] if 'showco' in names else []
    )
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
    stopped = [update.run_service_step(n, 'stop', run_command) for n in reversed(names)]
    if not all(r.ok for r in stopped):
        update.report_failures(stopped, output)
        update.report_failures(
            [update.run_service_step(n, 'start', run_command) for n in names], output
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
        if all(r.ok for r in results):
            results.extend(
                update.start_or_refresh_service_step(
                    n, refresh, root, provision_config, run_command
                )
                for n in names
            )
        if all(r.ok for r in results):
            results.extend(
                check_updated_services(names, root, provision_config, run_command)
            )
        if all(r.ok for r in results):
            return 0

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
    stopped = [update.run_service_step(n, 'stop', run_command) for n in reversed(names)]
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
                settings_path.write_bytes(saved_settings)
        except OSError as error:
            print(
                f'Rollback incomplete: cannot restore recs settings: {error}',
                file=output,
            )
            return 1
    if not all(r.ok for r in restored):
        print('Rollback incomplete; affected services remain stopped.', file=output)
        return 1
    restarted = [
        update.start_or_refresh_service_step(
            n, refresh, root, provision_config, run_command
        )
        for n in names
    ]
    restarted.extend(check_updated_services(names, root, provision_config, run_command))
    update.report_failures(restarted, output)
    if all(r.ok for r in restarted):
        print('Previous versions restored and services restarted.', file=output)
    else:
        print('Previous versions restored, but service recovery failed.', file=output)
    return 1


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
    return results
