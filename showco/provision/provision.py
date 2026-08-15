#!/usr/bin/env python3
from __future__ import annotations

import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from typing import Annotated

import tyro
from pydantic import BaseModel
from reccy import subprocess

from .. import machine_role, network_config, recs
from . import config

PROVISION_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (PROVISION_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()
REMOTE_GITHUB_KEY_TEMPLATE = "remote_github_key.tmpl.sh"
REBOOT_WAIT_SECONDS = 300
POST_REBOOT_READY_WAIT_SECONDS = 60
SSH_CONNECT_TIMEOUT_SECONDS = 2
SSH_VERIFICATION_TIMEOUT_SECONDS = 15
LOCAL_REPOSITORIES = ["showco", "reccy", "recs", "twitcho", "lyte"]
WIFI_STATUS_COMMAND = "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status"
STARTUP_CHECK_NAMES = [
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
    update: bool = True


def main(argv: list[str] | None = None) -> int:
    machine_role.require_provisioning_machine("showco provision")
    options = tyro.cli(
        ProvisionOptions,
        args=argv,
        description="Provision a reachable Raspberry Pi over SSH",
    )
    return run(options)


def run(options: ProvisionOptions) -> int:
    if options.host is not None:
        persist_network_host(options.config_path, options.host)
    if options.root is not None:
        persist_paths_root(options.config_path, options.root)
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
    validate_local_repositories()
    ssh_target = f"{parsed_config.network.user}@{parsed_config.network.host}"
    remote_script = "/tmp/showco-provision-pi.sh"

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        prefix="showco-provision-pi.",
        suffix=".sh",
    ) as fp:
        local_script = Path(fp.name)
        fp.write(REMOTE_SCRIPT)
    try:
        provision_remote(parsed_config, ssh_target, local_script, remote_script)
    finally:
        local_script.unlink(missing_ok=True)

    print(f"Provisioned {ssh_target}.")
    if options.update:
        from .. import update

        return update.update_from_provisioning_machine(
            update.selected_repositories([]),
            host=parsed_config.network.host,
            root=parsed_config.paths.root,
            target_config=parsed_config,
        )
    return 0


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
    ssh_target: str,
    local_script: Path,
    remote_script: str,
) -> None:
    uploaded = False
    print(f"Waiting for SSH connection to {ssh_target}...")
    wait_for_ssh(provision_config, ssh_target)
    topology = preflight_network_config(provision_config, ssh_target)
    print(f"Checking {ssh_target}...")
    run_ssh(
        provision_config,
        ssh_target,
        "set -e; uname -a; id; command -v sudo; command -v apt-get",
    )
    ensure_github_account_key(provision_config, ssh_target)

    try:
        print("Copying provisioning script...")
        run_scp(provision_config, local_script, f"{ssh_target}:{remote_script}")
        uploaded = True

        print(f"Running provisioning on {ssh_target}...")
        run_ssh(
            provision_config,
            ssh_target,
            remote_command(provision_config, remote_script),
        )

        print(f"Waiting for {ssh_target} to reboot...")
        wait_for_rebooted_ssh(provision_config, ssh_target)

        print(f"Checking provisioned services on {ssh_target}...")
        report_verification_results(
            wait_for_provisioning_ready(provision_config, ssh_target, topology)
        )
    finally:
        if uploaded:
            try:
                run_ssh(
                    provision_config,
                    ssh_target,
                    f"rm -f {shlex.quote(remote_script)}",
                    exit_on_error=False,
                )
            except CalledProcessError as e:
                if sys.exc_info()[0] is None:
                    raise
                print(
                    f"WARNING: Could not remove remote provisioning script: {e}",
                    file=sys.stderr,
                )


def preflight_network_config(
    provision_config: config.Config, ssh_target: str
) -> network_config.NetworkTopology:
    print(f"Checking Wi-Fi interfaces on {ssh_target}...")
    status = capture_ssh(provision_config, ssh_target, WIFI_STATUS_COMMAND)
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


def local_repository_errors(root: Path) -> list[str]:
    errors = []
    for repository in local_repositories(root):
        errors.extend(repository_errors(repository))
    return errors


def local_repositories(root: Path) -> list[LocalRepository]:
    return [LocalRepository(name=n, path=root / n) for n in LOCAL_REPOSITORIES]


def repository_errors(repository: LocalRepository) -> list[str]:
    if not repository.path.exists():
        return [f"- {repository.name}: {repository.path} does not exist"]
    try:
        git_output(repository.path, ["rev-parse", "--is-inside-work-tree"])
    except CalledProcessError as e:
        return [f"- {repository.name}: {repository.path} is not a Git repository: {e}"]

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


def git_push(repository: Path, upstream: str) -> None:
    remote, _, branch = upstream.partition("/")
    if not remote or not branch:
        raise CalledProcessError(
            128,
            ["git", "push"],
            stderr=f"bad upstream {upstream}",
        )
    subprocess.run(
        ["git", "-C", str(repository), "push", remote, f"HEAD:{branch}"],
        capture_output=True,
        check=True,
        text=True,
    )


def git_error_output(error: CalledProcessError) -> str:
    output = f"{error.stderr or ''}{error.stdout or ''}".strip()
    if output:
        return output
    return str(error)


def wait_for_rebooted_ssh(provision_config: config.Config, ssh_target: str) -> None:
    wait_for_ssh_disconnect(provision_config, ssh_target)
    wait_for_ssh(provision_config, ssh_target, timeout_seconds=REBOOT_WAIT_SECONDS)


def wait_for_ssh_disconnect(provision_config: config.Config, ssh_target: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not ssh_is_reachable(provision_config, ssh_target):
            return
        time.sleep(1)
    sys.exit(f"ERROR: {ssh_target} did not drop SSH before reboot")


def wait_for_ssh(
    provision_config: config.Config,
    ssh_target: str,
    *,
    timeout_seconds: int | None = None,
) -> None:
    deadline = None
    if timeout_seconds is not None:
        deadline = time.monotonic() + timeout_seconds
    while deadline is None or time.monotonic() < deadline:
        if ssh_is_reachable(provision_config, ssh_target):
            return
        time.sleep(1)
    sys.exit(f"ERROR: {ssh_target} did not accept SSH within {timeout_seconds}s")


def ssh_is_reachable(provision_config: config.Config, ssh_target: str) -> bool:
    completed = subprocess.run(
        ssh_command(provision_config, ssh_target, "true", connect_timeout=1),
        capture_output=True,
        check=False,
        text=True,
    )
    if has_changed_host_key(completed):
        remove_known_host(provision_config, ssh_target)
        return False
    return completed.returncode == 0


def has_changed_host_key(completed: CompletedProcess[str]) -> bool:
    output = f"{completed.stdout}{completed.stderr}"
    return "REMOTE HOST IDENTIFICATION HAS CHANGED" in output


def remove_known_host(provision_config: config.Config, ssh_target: str) -> None:
    for host in known_host_names(provision_config, ssh_target):
        print(f"Removing stale SSH host key for {host}...")
        subprocess.run(
            ["ssh-keygen", "-R", host],
            capture_output=True,
            check=False,
            text=True,
        )


def known_host_names(provision_config: config.Config, ssh_target: str) -> list[str]:
    host = ssh_target.rsplit("@", maxsplit=1)[-1]
    if provision_config.network.ssh_port == 22:
        return [host]
    return [host, f"[{host}]:{provision_config.network.ssh_port}"]


def verify_provisioning(
    provision_config: config.Config,
    ssh_target: str,
    topology: network_config.NetworkTopology | None = None,
) -> list[VerificationResult]:
    private_wifi_verification = []
    if topology is not None and topology != network_config.NetworkTopology.PUBLIC:
        if config.x18(provision_config) is not None:
            bridge_address = network_config.x18_bridge_address(provision_config)
            private_wifi_verification.append(
                verify_remote_command(
                    provision_config,
                    ssh_target,
                    "X18 bridge has the configured address",
                    "ip -4 -o address show dev "
                    f"{network_config.X18_BRIDGE_INTERFACE} "
                    f"| grep -F {shlex.quote(bridge_address)}",
                )
            )
        private_wifi_verification.append(
            verify_remote_command(
                provision_config,
                ssh_target,
                "private Wi-Fi hotspot is active",
                "nmcli -t -f TYPE,STATE,CONNECTION device status "
                f"| grep -F -x "
                f"'wifi:connected:{network_config.PRIVATE_WIFI_CONNECTION}'",
            )
        )
    return [
        verify_remote_command(
            provision_config,
            ssh_target,
            "no failed systemd units",
            "systemctl --failed --no-legend --plain",
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "reccy project status is clean",
            project_status_command("reccy", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "recs project status is clean",
            project_status_command("recs", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "twitcho project status is clean",
            project_status_command("twitcho", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "lyte project status is clean",
            project_status_command("lyte", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "showco project status is clean",
            project_status_command("showco", provision_config.paths.root),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "recs service is active",
            showco_service_status_command("recs", provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "recs status is advancing",
            recs.status_changes_command(),
            summarize_error=recs.status_failure_summary,
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "showco service is active",
            showco_service_status_command("showco", provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "showco web UI revision",
            showco_revision_command(provision_config.paths.root),
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "showco journal is readable",
            user_session_command(
                "journalctl --user -u showco.service -n 100 --no-pager >/dev/null"
            ),
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "NetworkManager device status is readable",
            "nmcli device status >/dev/null",
        ),
        verify_remote_command(
            provision_config,
            ssh_target,
            "NetworkManager connection list is readable",
            "nmcli connection show >/dev/null",
        ),
        *private_wifi_verification,
        verify_lyte_midi_service(provision_config, ssh_target),
        verify_twitcho_service(provision_config, ssh_target),
        verify_x18_usb_device(provision_config, ssh_target),
    ]


def wait_for_provisioning_ready(
    provision_config: config.Config,
    ssh_target: str,
    topology: network_config.NetworkTopology,
) -> list[VerificationResult]:
    deadline = time.monotonic() + POST_REBOOT_READY_WAIT_SECONDS
    while True:
        results = verify_provisioning(provision_config, ssh_target, topology)
        startup_errors = [
            r for r in results if r.name in STARTUP_CHECK_NAMES and r.error
        ]
        if not startup_errors or time.monotonic() >= deadline:
            return results
        time.sleep(1)


def project_status_command(project: str, root: Path) -> str:
    return f"git -C {shlex.quote(str(root / project))} status --short"


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
        'password=$(cat "$HOME/.config/showco/control-password") && '
        "curl --fail --silent --show-error --max-time 5 "
        '--user "showco:$password" http://127.0.0.1:17352/status | '
        'grep --fixed-strings "\\"revision\\":\\"$expected\\""'
    )


def user_session_command(command: str) -> str:
    return f"uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid {command}"


def verify_lyte_midi_service(
    provision_config: config.Config, ssh_target: str
) -> VerificationResult:
    if not provision_config.lyte.enabled:
        return VerificationResult(name="Lyte MIDI service", error="", note="disabled")
    installed = verify_remote_command(
        provision_config,
        ssh_target,
        "Lyte MIDI service is installed",
        user_systemctl_command("is-enabled --quiet lyte-midi.service"),
    )
    if installed.error:
        return installed
    active = verify_remote_command(
        provision_config,
        ssh_target,
        "Lyte MIDI service",
        user_systemctl_command("is-active --quiet lyte-midi.service"),
    )
    if not active.error:
        return active
    return active


def verify_twitcho_service(
    provision_config: config.Config, ssh_target: str
) -> VerificationResult:
    if not provision_config.twitch.enabled:
        return VerificationResult(name="Twitcho service", error="", note="disabled")
    return verify_remote_command(
        provision_config,
        ssh_target,
        "Twitcho service",
        showco_service_status_command("twitcho", provision_config.paths.root),
    )


def verify_x18_usb_device(
    provision_config: config.Config, ssh_target: str
) -> VerificationResult:
    if (
        not provision_config.usb.x18_device_name
        or provision_config.usb.x18_device_name == "TODO"
    ):
        return VerificationResult(
            name="X18 USB device", error="", note="not configured"
        )
    device_names = x18_device_names(provision_config.usb.x18_device_name)
    selectors = " ".join(f"-e {shlex.quote(name)}" for name in device_names)
    command = f"arecord -l | grep -Fi {selectors} >/dev/null"
    try:
        completed = subprocess.run(
            ssh_command(provision_config, ssh_target, command, connect_timeout=1),
            capture_output=True,
            check=False,
            text=True,
            timeout=SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return VerificationResult(
            name="X18 USB device",
            error="",
            note=f"detection timed out after {SSH_VERIFICATION_TIMEOUT_SECONDS}s",
        )
    if completed.returncode == 0:
        return VerificationResult(name="X18 USB device", error="")
    return VerificationResult(
        name="X18 USB device",
        error="",
        note=f"{provision_config.usb.x18_device_name} not detected",
    )


def x18_device_names(value: str) -> list[str]:
    return [name for part in value.split("/") if (name := part.strip())]


def verify_remote_command(
    provision_config: config.Config,
    ssh_target: str,
    name: str,
    command: str,
    *,
    expect_empty_stdout: bool = False,
    summarize_error: Callable[[str], str] | None = None,
) -> VerificationResult:
    try:
        completed = subprocess.run(
            ssh_command(provision_config, ssh_target, command, connect_timeout=1),
            capture_output=True,
            check=False,
            text=True,
            timeout=SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return VerificationResult(
            name=name,
            error=f"command timed out after {SSH_VERIFICATION_TIMEOUT_SECONDS}s",
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


def ensure_github_account_key(provision_config: config.Config, ssh_target: str) -> None:
    if not shutil.which("gh"):
        sys.exit(
            "ERROR: gh is required on the provisioning machine "
            "to add the Pi SSH key to GitHub."
        )
    print("Creating or reusing Raspberry Pi GitHub SSH key...")
    public_key = capture_ssh(
        provision_config, ssh_target, remote_github_key_command(provision_config)
    )
    if not public_key.startswith("ssh-ed25519 "):
        sys.exit(f"ERROR: Unexpected SSH public key from {ssh_target}: {public_key}")
    title = github_key_title(provision_config)
    if github_key_exists(public_key):
        print(f"GitHub SSH key already exists: {title}")
        return
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        prefix="showco-pi-github-key.",
        suffix=".pub",
    ) as fp:
        key_file = Path(fp.name)
        fp.write(public_key + "\n")
    try:
        add_github_key(key_file, title)
    finally:
        key_file.unlink(missing_ok=True)


def add_github_key(key_file: Path, title: str) -> None:
    try:
        subprocess.run(
            ["gh", "ssh-key", "add", str(key_file), "--title", title],
            capture_output=True,
            check=True,
            text=True,
        )
    except CalledProcessError as e:
        sys.exit(
            gh_error_message(
                "Could not add the Pi SSH key to GitHub from the provisioning machine.",
                e,
            )
        )


def github_key_exists(public_key: str) -> bool:
    try:
        completed = subprocess.run(
            ["gh", "api", "user/keys", "--jq", ".[].key"],
            capture_output=True,
            check=True,
            text=True,
        )
    except CalledProcessError as e:
        sys.exit(
            gh_error_message(
                "Could not list GitHub SSH keys from the provisioning machine.",
                e,
            )
        )
    key = github_key_material(public_key)
    return any(
        github_key_material(line) == key for line in completed.stdout.splitlines()
    )


def gh_error_message(message: str, error: CalledProcessError) -> str:
    details = (error.stderr or error.stdout or "").strip()
    result = f"ERROR: {message} Run `gh auth status` on this machine."
    if details:
        result += f"\ngh said: {details}"
    return result


def github_key_material(public_key: str) -> str:
    fields = public_key.split()
    if len(fields) < 2:
        return public_key
    return " ".join(fields[:2])


def github_key_title(provision_config: config.Config) -> str:
    return f"showco {provision_config.network.host}"


def remote_github_key_command(provision_config: config.Config) -> str:
    comment = shlex.quote(github_key_title(provision_config))
    template = script_dir() / REMOTE_GITHUB_KEY_TEMPLATE
    return template.read_text().replace("{comment}", comment)


def shell_bool(value: bool) -> str:
    return "true" if value else "false"


def remote_command(provision_config: config.Config, remote_script: str) -> str:
    private = config.internal_wifi(provision_config)
    external = config.external_wifi(provision_config)
    x18_network = config.x18(provision_config)
    x18_host = ""
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
        "SHOWCO_CONTROL_PASSWORD": private.password,
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
        "X18_USB_DEVICE_NAME": provision_config.usb.x18_device_name,
    }
    assignments = [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def run_ssh(
    provision_config: config.Config,
    target: str,
    command: str,
    *,
    exit_on_error: bool = True,
) -> None:
    try:
        run_command(
            ssh_command(provision_config, target, command, allocate_tty=True),
        )
    except CalledProcessError as e:
        if not exit_on_error:
            raise
        sys.exit(ssh_error_message(target, e))


def run_scp(provision_config: config.Config, source: Path, target: str) -> None:
    try:
        run_command(
            [
                "scp",
                "-o",
                f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
                "-P",
                str(provision_config.network.ssh_port),
                str(source),
                target,
            ],
        )
    except CalledProcessError as e:
        sys.exit(ssh_error_message(target, e))


def capture_ssh(provision_config: config.Config, target: str, command: str) -> str:
    try:
        completed = run_command(
            ssh_command(provision_config, target, command),
            capture_output=True,
        )
    except CalledProcessError as e:
        sys.exit(ssh_error_message(target, e))
    return completed.stdout.strip()


def ssh_error_message(target: str, error: CalledProcessError) -> str:
    output = f"{error.stdout or ''}{error.stderr or ''}".strip()
    message = (
        f"ERROR: SSH connection or command failed for {target}. "
        f"SSH connect timeout is {SSH_CONNECT_TIMEOUT_SECONDS} seconds."
    )
    if output:
        message += f"\nssh said: {output}"
    return message


def ssh_command(
    provision_config: config.Config,
    target: str,
    command: str,
    *,
    allocate_tty: bool = False,
    connect_timeout: int | None = SSH_CONNECT_TIMEOUT_SECONDS,
) -> list[str]:
    result = ["ssh"]
    if allocate_tty:
        result.append("-t")
    if connect_timeout is not None:
        result.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    result.extend(["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"])
    result.extend(["-p", str(provision_config.network.ssh_port), target, command])
    return result


def run_command(
    command: list[str],
    *,
    capture_output: bool = False,
) -> CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=capture_output,
        check=True,
        text=True,
    )


def script_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
