#!/usr/bin/env python3
from __future__ import annotations

import shlex
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired
from typing import Annotated

import tyro
from pydantic import BaseModel
from reccy import subprocess

from .. import machine_role, network_config, recs
from . import config, script, ssh

PROVISION_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (PROVISION_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()
POST_REBOOT_READY_WAIT_SECONDS = 60
SSH_CLEANUP_TIMEOUT_SECONDS = 15
REMOTE_PROVISION_TIMEOUT_SECONDS = 1_800
LOCAL_REPOSITORIES = ["showco", "reccy", "recs", "twitcho", "lyte"]
WIFI_STATUS_COMMAND = "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status"
STARTUP_CHECK_NAMES = [
    "Lyte service",
    "recs service is active",
    "recs status is advancing",
    "showco service is active",
    "showco web UI revision",
    "Twitcho service",
    "X18 bridge has the configured address",
    "private Wi-Fi hotspot is active",
]


class VerificationResult(BaseModel, frozen=True):
    name: str
    error: str
    note: str = ""


class LocalRepository(BaseModel, frozen=True):
    name: str
    path: Path


class ProvisionOptions(BaseModel, frozen=True):
    config_path: Annotated[
        Path,
        tyro.conf.arg(name="config"),
    ] = PROVISION_DIR / "config.toml"
    secrets: Path = PROVISION_DIR / "secrets.toml"
    host: str | None = None
    user: str | None = None
    port: int | None = None
    root: Path | None = None
    reccy_repo: str | None = None
    recs_repo: str | None = None
    twitcho_repo: str | None = None
    showco_repo: str | None = None
    lyte_repo: str | None = None
    lyte_enabled: bool | None = None
    lyte_daemon_config: Path | None = None


def main(argv: list[str] | None = None) -> int:
    machine_role.require_provisioning_machine("showco provision")
    options = tyro.cli(
        ProvisionOptions,
        args=argv,
        description="Provision a reachable Raspberry Pi over SSH",
    )
    return run(options)


def run(options: ProvisionOptions) -> int:
    env = config.merge_values(
        config.read_toml(options.config_path), config.read_toml(options.secrets)
    )
    parsed_config = config.config_from_values(
        env,
        host=options.host,
        user=options.user,
        port=options.port,
        root=options.root,
        reccy_repo=options.reccy_repo,
        recs_repo=options.recs_repo,
        twitcho_repo=options.twitcho_repo,
        showco_repo=options.showco_repo,
        lyte_repo=options.lyte_repo,
        lyte_enabled=options.lyte_enabled,
        lyte_daemon_config=options.lyte_daemon_config,
    )
    validate_config(parsed_config)
    autosquash_local_repositories()
    validate_local_worktrees()
    validate_local_repositories()
    if options.host is not None:
        persist_network_host(options.config_path, options.host)
    if options.root is not None:
        persist_paths_root(options.config_path, options.root)
    remote_script = "/tmp/showco-provision-pi.sh"

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        prefix="showco-provision-pi.",
        suffix=".sh",
    ) as fp:
        local_script = Path(fp.name)
        fp.write(script.REMOTE_SCRIPT)
    try:
        provision_remote(
            parsed_config,
            local_script,
            remote_script,
        )
    finally:
        local_script.unlink(missing_ok=True)

    print(f"Provisioned {parsed_config.ssh_target}.")
    return 0


def autosquash_local_repositories() -> None:
    from .. import update

    programs = update.programs_for_repositories(
        update.REPOSITORY_NAMES,
        local_checkout_directory(),
    )
    if not update.autosquash_programs(
        programs,
        50,
        update.run_command_with_timeout,
        sys.stdout,
    ):
        sys.exit("ERROR: could not autosquash local repositories before provisioning")


def persist_network_host(config_path: Path, host: str) -> None:
    persist_config_value(config_path, "network", "host", host)


def persist_paths_root(config_path: Path, root: Path) -> None:
    persist_config_value(config_path, "paths", "root", str(root))


def persist_config_value(config_path: Path, table: str, key: str, value: str) -> None:
    path = config_path.expanduser()
    lines = path.read_text().splitlines()
    value_line = f"{key} = {toml_string(value)}"
    table_index_value = table_index(lines, f"[{table}]")
    if table_index_value is None:
        path.write_text(f"[{table}]\n" + value_line + "\n\n" + "\n".join(lines) + "\n")
        return
    next_table = next_table_index(lines, table_index_value + 1)
    for index in range(table_index_value + 1, next_table):
        if lines[index].lstrip().startswith(key):
            lines[index] = value_line
            path.write_text("\n".join(lines) + "\n")
            return
    lines.insert(table_index_value + 1, value_line)
    path.write_text("\n".join(lines) + "\n")


def table_index(lines: list[str], table: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == table:
            return index
    return None


def next_table_index(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        line = lines[index].strip()
        if line.startswith("[") and line.endswith("]"):
            return index
    return len(lines)


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def provision_remote(
    provision_config: config.Config,
    local_script: Path,
    remote_script: str,
) -> None:
    uploaded = False
    if provision_config.accept_changed_host_key:
        ssh.remove_known_host(provision_config)
    print(f"Waiting for SSH connection to {provision_config.ssh_target}...")
    ssh.wait_for_ssh(provision_config)
    validate_remote_worktrees(provision_config)
    topology = preflight_network_config(provision_config)
    print(f"Checking {provision_config.ssh_target}...")
    ssh.run_ssh(
        provision_config,
        "set -e; uname -a; id; command -v sudo; command -v apt-get",
        timeout_seconds=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
    )
    try:
        print("Copying provisioning script...")
        ssh.run_scp(provision_config, local_script, remote_script)
        uploaded = True

        print(f"Running provisioning on {provision_config.ssh_target}...")
        ssh.run_ssh(
            provision_config,
            script.remote_command(provision_config, remote_script),
            timeout_seconds=REMOTE_PROVISION_TIMEOUT_SECONDS,
        )

        if ssh.provisioning_reboot_required(provision_config):
            print(f"Waiting for {provision_config.ssh_target} to reboot...")
            ssh.schedule_remote_reboot(provision_config)
            ssh.wait_for_rebooted_ssh(provision_config)
        else:
            print(f"No reboot is required for {provision_config.ssh_target}.")

        print(f"Checking provisioned services on {provision_config.ssh_target}...")
        report_verification_results(
            wait_for_provisioning_ready(provision_config, topology)
        )
    finally:
        if uploaded:
            try:
                ssh.run_ssh(
                    provision_config,
                    f"rm -f {shlex.quote(remote_script)}",
                    exit_on_error=False,
                    timeout_seconds=SSH_CLEANUP_TIMEOUT_SECONDS,
                )
            except CalledProcessError as e:
                if sys.exc_info()[0] is None:
                    raise
                print(
                    f"WARNING: Could not remove remote provisioning script: {e}",
                    file=sys.stderr,
                )


def preflight_network_config(
    provision_config: config.Config,
) -> network_config.NetworkTopology:
    print(f"Checking Wi-Fi interfaces on {provision_config.ssh_target}...")
    status = ssh.capture_ssh(provision_config, WIFI_STATUS_COMMAND)
    interfaces = network_config.wifi_interfaces_from_status(status)
    assignment = network_config.assign_wifi(
        interfaces, provision_config.network.swap_wifi
    )
    topology = network_config.select_topology(
        provision_config, assignment.secondary is not None
    )
    network_config.network_commands(provision_config, assignment, topology)
    return topology


def validate_config(provision_config: config.Config) -> None:
    errors = config_errors(provision_config)
    if not errors:
        return
    sys.exit("ERROR: invalid provisioning configuration\n" + "\n".join(errors))


def config_errors(provision_config: config.Config) -> list[str]:
    errors = []
    external = config.external_wifi(provision_config)
    private = config.internal_wifi(provision_config)
    if not external.name or external.name == "TODO":
        errors.append("- networks.external.wifi.external.name is required")
    if not private.password or private.password == "TODO":
        errors.append("- networks.internal.wifi.private.password is required")
    if (
        provision_config.lyte.enabled
        and not lyte_daemon_config_path(
            provision_config, local_checkout_directory()
        ).is_file()
    ):
        errors.append(
            "- lyte.daemon_config does not exist: "
            f"{lyte_daemon_config_path(provision_config, local_checkout_directory())}"
        )
    return errors


def lyte_daemon_config_path(provision_config: config.Config, root: Path) -> Path:
    return root / "lyte" / provision_config.lyte.daemon_config


def validate_local_repositories(root: Path | None = None) -> None:
    errors = local_repository_errors(root or local_checkout_directory())
    if errors:
        sys.exit(
            "ERROR: local repositories are not ready for Raspberry Pi provisioning\n"
            + "\n".join(errors)
        )


def validate_local_worktrees(root: Path | None = None) -> None:
    errors = local_worktree_errors(root or local_checkout_directory())
    if errors:
        sys.exit("ERROR: local repositories have tracked changes\n" + "\n".join(errors))


def local_worktree_errors(root: Path) -> list[str]:
    errors = []
    for repository in local_repositories(root):
        errors.extend(repository_worktree_errors(repository))
    return errors


def local_repository_errors(root: Path) -> list[str]:
    errors = []
    for repository in local_repositories(root):
        errors.extend(repository_errors(repository))
    return errors


def local_repositories(root: Path) -> list[LocalRepository]:
    return [LocalRepository(name=n, path=root / n) for n in LOCAL_REPOSITORIES]


def repository_errors(repository: LocalRepository) -> list[str]:
    if errors := repository_worktree_errors(repository):
        return errors

    errors = []
    try:
        upstream = git_output(
            repository.path,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        )
    except CalledProcessError:
        errors.append(f"- {repository.name}: current branch has no upstream")
        return errors
    ahead = int(
        git_output(repository.path, ["rev-list", "--count", "@{upstream}..HEAD"])
    )
    if ahead:
        try:
            git_push(repository.path, upstream)
        except CalledProcessError as e:
            errors.append(
                f"- {repository.name}: could not push {ahead} local commit(s) "
                f"to {upstream}: {git_error_output(e)}"
            )
            return errors
        remaining = int(
            git_output(repository.path, ["rev-list", "--count", "@{upstream}..HEAD"])
        )
        if remaining:
            errors.append(
                f"- {repository.name}: {remaining} local commit(s) are still not "
                f"in {upstream}"
            )
    return errors


def repository_worktree_errors(repository: LocalRepository) -> list[str]:
    if not repository.path.exists():
        return [f"- {repository.name}: {repository.path} does not exist"]
    try:
        git_output(repository.path, ["rev-parse", "--is-inside-work-tree"])
    except CalledProcessError as e:
        return [f"- {repository.name}: {repository.path} is not a Git repository: {e}"]
    try:
        status = git_status_output(repository.path)
    except CalledProcessError as e:
        return [f"- {repository.name}: git status failed: {git_error_output(e)}"]
    if not status:
        return []
    return [f"- {repository.name}:\n{status}"]


def validate_remote_worktrees(provision_config: config.Config) -> None:
    print(f"Checking target repository worktrees on {provision_config.ssh_target}...")
    ssh.run_ssh(
        provision_config,
        remote_worktree_command(provision_config.paths.root),
    )


def remote_worktree_command(root: Path) -> str:
    names = " ".join(LOCAL_REPOSITORIES)
    script = f"""root={shlex.quote(str(root))}
failed=false
for name in {names}; do
  path="$root/$name"
  if [[ -e "$path" && ! -d "$path/.git" ]]; then
    printf '%s: not a Git checkout: %s\\n' "$name" "$path"
    failed=true
    continue
  fi
  if [[ ! -d "$path/.git" ]]; then
    continue
  fi
  if ! status=$(git -C "$path" status --short --untracked-files=no); then
    printf '%s: git status failed\\n' "$name"
    failed=true
    continue
  fi
  status=$(printf '%s\\n' "$status" | sed -E '/^.. (.*\\/)?uv\\.lock$/d')
  if [[ -n "$status" ]]; then
    printf '%s:\\n%s\\n' "$name" "$status"
    failed=true
  fi
done
if [[ "$failed" == true ]]; then
  exit 1
fi"""
    return f"bash -c {shlex.quote(script)}"


def local_checkout_directory() -> Path:
    return PROVISION_DIR.parents[2]


def git_output(repository: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def git_status_output(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "status", "--short", "--untracked-files=no"],
        capture_output=True,
        check=True,
        text=True,
    )
    return "\n".join(
        line
        for line in completed.stdout.splitlines()
        if line[3:].rsplit("/", maxsplit=1)[-1] != "uv.lock"
    )


def git_push(repository: Path, upstream: str) -> None:
    remote, _, branch = upstream.partition("/")
    if not remote or not branch:
        raise CalledProcessError(
            128,
            ["git", "push"],
            stderr=f"bad upstream {upstream}",
        )
    command = ["git", "-C", str(repository)]
    try:
        subprocess.run(
            [*command, "push", remote, f"HEAD:{branch}"],
            capture_output=True,
            check=True,
            text=True,
        )
        return
    except CalledProcessError as error:
        push_error = error
    try:
        subprocess.run(
            [
                *command,
                "fetch",
                remote,
                f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        upstream_commit = git_output(
            repository,
            ["rev-parse", f"{remote}/{branch}"],
        )
        subprocess.run(
            [
                *command,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{upstream_commit}",
                remote,
                f"HEAD:{branch}",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
    except CalledProcessError as recovery_error:
        raise CalledProcessError(
            recovery_error.returncode,
            recovery_error.cmd,
            output=recovery_error.stdout,
            stderr=(
                f"regular push failed:\n{git_error_output(push_error)}\n"
                f"force-with-lease recovery failed:\n{git_error_output(recovery_error)}"
            ),
        ) from recovery_error


def git_error_output(error: CalledProcessError) -> str:
    output = f"{error.stderr or ''}{error.stdout or ''}".strip()
    if output:
        return output
    return str(error)


def verify_provisioning(
    provision_config: config.Config,
    topology: network_config.NetworkTopology | None = None,
) -> list[VerificationResult]:
    private_wifi_verification = []
    if topology is not None and topology != network_config.NetworkTopology.PUBLIC:
        if config.x18(provision_config) is not None:
            bridge_address = network_config.x18_bridge_address(provision_config)
            private_wifi_verification.append(
                verify_remote_command(
                    provision_config,
                    "X18 bridge has the configured address",
                    "ip -4 -o address show dev "
                    f"{network_config.X18_BRIDGE_INTERFACE} "
                    f"| grep -F {shlex.quote(bridge_address)}",
                )
            )
        private_wifi_verification.append(
            verify_remote_command(
                provision_config,
                "private Wi-Fi hotspot is active",
                "nmcli -t -f TYPE,STATE,CONNECTION device status "
                f"| grep -F -x "
                f"'wifi:connected:{network_config.PRIVATE_WIFI_CONNECTION}'",
            )
        )
    return [
        verify_remote_command(
            provision_config,
            "no failed systemd units",
            "systemctl --failed --no-legend --plain",
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "reccy project status is clean",
            project_status_command("reccy", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "recs project status is clean",
            project_status_command("recs", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "twitcho project status is clean",
            project_status_command("twitcho", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "lyte project status is clean",
            project_status_command("lyte", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "showco project status is clean",
            project_status_command("showco", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            "recs service is active",
            showco_service_status_command("recs", provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            "recs status is advancing",
            recs.status_changes_command(),
            summarize_error=recs.status_failure_summary,
        ),
        verify_remote_command(
            provision_config,
            "showco service is active",
            showco_service_status_command("showco", provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            "showco web UI revision",
            showco_revision_command(provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            "persistent journal is readable",
            persistent_journal_command(),
        ),
        verify_remote_command(
            provision_config,
            "NetworkManager device status is readable",
            "nmcli device status >/dev/null",
        ),
        verify_remote_command(
            provision_config,
            "NetworkManager connection list is readable",
            "nmcli connection show >/dev/null",
        ),
        *private_wifi_verification,
        verify_lyte_service(provision_config),
        verify_twitcho_service(provision_config),
        *verify_mixer_devices(provision_config),
    ]


def wait_for_provisioning_ready(
    provision_config: config.Config,
    topology: network_config.NetworkTopology,
) -> list[VerificationResult]:
    deadline = time.monotonic() + POST_REBOOT_READY_WAIT_SECONDS
    while True:
        results = verify_provisioning(provision_config, topology)
        startup_errors = [
            r for r in results if r.name in STARTUP_CHECK_NAMES and r.error
        ]
        if not startup_errors or time.monotonic() >= deadline:
            return results
        time.sleep(1)


def project_status_command(project: str, root: Path) -> str:
    return (
        f"git -C {shlex.quote(str(root / project))} status --short "
        "| sed -E '/^.. (.*\\/)?uv\\.lock$/d'"
    )


def user_systemctl_command(arguments: str) -> str:
    return user_session_command(f"systemctl --user {arguments}")


def showco_service_status_command(service: str, root: Path) -> str:
    return user_session_command(
        f'cd {shlex.quote(str(root / "showco"))} && PATH="$HOME/.local/bin:$PATH" '
        f"uv run --frozen showco run service-status {service}"
    )


def showco_revision_command(root: Path) -> str:
    showco_directory = shlex.quote(str(root / "showco"))
    return (
        f"expected=$(git -C {showco_directory} rev-parse HEAD) && "
        "curl --fail --silent --show-error --max-time 5 "
        "http://127.0.0.1:17352/status | "
        'grep --fixed-strings "\\"revision\\":\\"$expected\\""'
    )


def showco_twitcho_health_command(root: Path) -> str:
    showco_directory = shlex.quote(str(root / "showco"))
    return user_session_command(
        f'cd {showco_directory} && PATH="$HOME/.local/bin:$PATH" '
        "uv run --frozen showco run twitcho-health"
    )


def persistent_journal_command() -> str:
    return (
        "sudo systemd-cat --identifier=showco-provisioning "
        "/usr/bin/printf '%s\\n' 'Showco journal check' && "
        "sudo journalctl -t showco-provisioning -n 1 --no-pager --output=cat "
        "| grep --fixed-strings --line-regexp 'Showco journal check'"
    )


def user_session_command(command: str) -> str:
    return f"uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid {command}"


def verify_lyte_service(provision_config: config.Config) -> VerificationResult:
    if not provision_config.lyte.enabled:
        return VerificationResult(name="Lyte MIDI service", error="", note="disabled")
    installed = verify_remote_command(
        provision_config,
        "Lyte service is installed",
        user_systemctl_command("is-enabled --quiet lyte.service"),
    )
    if installed.error:
        return installed
    active = verify_remote_command(
        provision_config,
        "Lyte service",
        user_systemctl_command("is-active --quiet lyte.service"),
    )
    if not active.error:
        return active
    return active


def verify_twitcho_service(provision_config: config.Config) -> VerificationResult:
    if not provision_config.twitch.enabled:
        return VerificationResult(name="Twitcho service", error="", note="disabled")
    return verify_remote_command(
        provision_config,
        "Twitcho service",
        showco_twitcho_health_command(provision_config.paths.root),
    )


def verify_mixer_devices(provision_config: config.Config) -> list[VerificationResult]:
    return [
        *(
            verify_mixer_audio_input(provision_config, mixer.name, name)
            for mixer in provision_config.mixers
            for name in mixer.audio_device_names
        ),
        *(
            verify_mixer_midi_input(provision_config, mixer.name, name)
            for mixer in provision_config.mixers
            for name in mixer.midi_input_names
        ),
    ]


def verify_mixer_audio_input(
    provision_config: config.Config, mixer_name: str, selector: str
) -> VerificationResult:
    command = f"arecord -l | grep -Fi -e {shlex.quote(selector)} >/dev/null"
    try:
        completed = subprocess.run(
            ssh.ssh_command(
                provision_config,
                provision_config.ssh_target,
                command,
                connect_timeout=1,
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return VerificationResult(
            name=f"{mixer_name} USB audio {selector}",
            error="",
            note=f"detection timed out after {ssh.SSH_VERIFICATION_TIMEOUT_SECONDS}s",
        )
    if completed.returncode == 0:
        return VerificationResult(name=f"{mixer_name} USB audio {selector}", error="")
    return VerificationResult(
        name=f"{mixer_name} USB audio {selector}",
        error="",
        note=f"{selector} not detected",
    )


def verify_mixer_midi_input(
    provision_config: config.Config, mixer_name: str, selector: str
) -> VerificationResult:
    matcher = (
        "from recs.midi.device import input_names; import sys; "
        "sys.exit(not any(name.startswith(sys.argv[1]) for name in input_names()))"
    )
    command = " ".join(
        [
            f"cd {shlex.quote(str(provision_config.paths.root / 'recs'))}",
            "&& uv run --frozen python -c",
            shlex.quote(matcher),
            shlex.quote(selector),
        ]
    )
    try:
        completed = subprocess.run(
            ssh.ssh_command(
                provision_config,
                provision_config.ssh_target,
                command,
                connect_timeout=1,
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return VerificationResult(
            name=f"{mixer_name} USB MIDI {selector}",
            error="",
            note=f"detection timed out after {ssh.SSH_VERIFICATION_TIMEOUT_SECONDS}s",
        )
    if completed.returncode == 0:
        return VerificationResult(name=f"{mixer_name} USB MIDI {selector}", error="")
    return VerificationResult(
        name=f"{mixer_name} USB MIDI {selector}",
        error="",
        note=f"{selector} not detected",
    )


def verify_remote_command(
    provision_config: config.Config,
    name: str,
    command: str,
    *,
    expect_empty_stdout: bool = False,
    summarize_error: Callable[[str], str] | None = None,
) -> VerificationResult:
    try:
        completed = subprocess.run(
            ssh.ssh_command(
                provision_config,
                provision_config.ssh_target,
                command,
                connect_timeout=1,
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return VerificationResult(
            name=name,
            error=f"command timed out after {ssh.SSH_VERIFICATION_TIMEOUT_SECONDS}s",
        )
    output = f"{completed.stdout}{completed.stderr}".strip()
    if completed.returncode == 0 and (not expect_empty_stdout or not output):
        return VerificationResult(name=name, error="")
    if summarize_error is not None:
        output = summarize_error(output)
    if not output:
        output = f"command exited with status {completed.returncode}"
    return VerificationResult(name=name, error=output)


def report_verification_results(results: list[VerificationResult]) -> None:
    errors = [r for r in results if r.error]
    notes = [r for r in results if r.note]
    if not errors:
        if notes:
            print("Notes:")
            for note in notes:
                print(f"- {note.name}: {note.note}")
        print("Success!")
        return
    print("ERROR")
    for error in errors:
        print(f"- {error.name}: {error.error}")
    if notes:
        print("Notes:")
        for note in notes:
            print(f"- {note.name}: {note.note}")
    sys.exit(1)


def shell_bool(value: bool) -> str:
    return "true" if value else "false"


def remote_command(provision_config: config.Config, remote_script: str) -> str:
    private = config.internal_wifi(provision_config)
    external = config.external_wifi(provision_config)
    x18_network = config.x18(provision_config)
    x18_mixer = next(
        (mixer for mixer in provision_config.mixers if mixer.name == "X18"), None
    )
    x18_host = x18_mixer.osc.host if x18_mixer and x18_mixer.osc else ""
    x18_subnet = "10.43.0.0/24"
    if x18_network is not None:
        x18_host = config.require_value(
            "networks.internal.wired.x18.ip_address",
            x18_network.ip_address,
        )
        x18_subnet = config.string_or_default(x18_network.subnet, "10.43.0.0/24")
    values = {
        "SHOW_USER": provision_config.network.user,
        "SHOWCO_HOST": provision_config.network.host,
        "ROOT": str(provision_config.paths.root),
        "RECCY_REPO": provision_config.git.reccy.url,
        "RECCY_REFNAME": provision_config.git.reccy.refname,
        "RECS_REPO": provision_config.git.recs.url,
        "RECS_REFNAME": provision_config.git.recs.refname,
        "TWITCHO_REPO": provision_config.git.twitcho.url,
        "TWITCHO_REFNAME": provision_config.git.twitcho.refname,
        "LYTE_REPO": provision_config.git.lyte.url,
        "LYTE_REFNAME": provision_config.git.lyte.refname,
        "SHOWCO_REPO": provision_config.git.showco.url,
        "SHOWCO_REFNAME": provision_config.git.showco.refname,
        "SHOWCO_PORT": str(provision_config.network.web_port),
        "X18": shell_bool(x18_network is not None),
        "SWAP_WIFI": shell_bool(provision_config.network.swap_wifi),
        "NETWORK_TOPOLOGY": provision_config.network.topology,
        "TWITCHO_ENABLED": shell_bool(provision_config.twitch.enabled),
        "LYTE_ENABLED": shell_bool(provision_config.lyte.enabled),
        "LYTE_DAEMON_CONFIG": str(provision_config.lyte.daemon_config),
        "PRIVATE_WIFI_SSID": config.string_or_default(private.name, "showbox"),
        "PRIVATE_WIFI_PASSWORD": private.password,
        "EXTERNAL_WIFI_SSID": external.name,
        "EXTERNAL_WIFI_PASSWORD": external.password,
        "SHOWCO_PI_X18_SUBNET": x18_subnet,
        "SHOWCO_X18_HOST": x18_host,
        "SHOWCO_MIXERS_TOML": mixers_toml(provision_config.mixers),
        "RECS_AUDIO_DEVICE_NAMES": "\n".join(
            unique_selectors(
                name
                for mixer in provision_config.mixers
                for name in mixer.audio_device_names
            )
        ),
        "RECS_MIDI_INPUT_NAMES": "\n".join(
            unique_selectors(
                name
                for mixer in provision_config.mixers
                for name in mixer.midi_input_names
            )
        ),
        "RECS_OSC_NODES_TOML": osc_nodes_toml(provision_config.mixers),
    }
    assignments = [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def mixers_toml(mixers: list[config.MixerSpec]) -> str:
    lines = []
    for mixer in mixers:
        lines.extend(["[[mixers]]", f"name = {mixer.name!r}"])
        if mixer.audio_device_names:
            lines.append(f"audio_device_names = {mixer.audio_device_names!r}")
        if mixer.midi_input_names:
            lines.append(f"midi_input_names = {mixer.midi_input_names!r}")
        if mixer.probe:
            lines.extend(
                [
                    "[mixers.probe]",
                    f"host = {mixer.probe.host!r}",
                    f"port = {mixer.probe.port}",
                    f"protocol = {mixer.probe.protocol!r}",
                ]
            )
        if mixer.osc:
            lines.extend(
                [
                    "[mixers.osc]",
                    f"host = {mixer.osc.host!r}",
                    f"port = {mixer.osc.port}",
                    f"subscription_path = {mixer.osc.subscription_path!r}",
                    f"resubscribe_period = {mixer.osc.resubscribe_period}",
                ]
            )
    return "\n".join(lines) + "\n"


def unique_selectors(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def osc_nodes_toml(mixers: list[config.MixerSpec]) -> str:
    lines = []
    for mixer in mixers:
        if mixer.osc is None:
            continue
        lines.extend(
            [
                "[[nodes]]",
                f"name = {mixer.name!r}",
                f"host = {mixer.osc.host!r}",
                f"port = {mixer.osc.port}",
                "",
                "[[nodes.subscriptions]]",
                f"path = {mixer.osc.subscription_path!r}",
                f"resubscribe_period = {mixer.osc.resubscribe_period}",
                "",
            ]
        )
    return "\n".join(lines)


def script_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
