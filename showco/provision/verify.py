from __future__ import annotations

import shlex
import sys
import time
from collections.abc import Callable
from pathlib import Path
from subprocess import TimeoutExpired

from pydantic import BaseModel
from reccy.runtime import subprocess

from .. import network_config, recs, revision
from . import config, ssh

POST_REBOOT_READY_WAIT_SECONDS = 60
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
            "target project statuses are clean",
            project_statuses_command(provision_config.paths.root),
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
            revision.showco_revision_command(
                provision_config.paths.root,
                provision_config.network.web_port,
                retry=False,
            ),
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
    return f"git -C {shlex.quote(str(root / project))} status --short"


def project_statuses_command(root: Path) -> str:
    root_value = shlex.quote(str(root))
    return (
        "for project in reccy recs twitcho lyte showco; do "
        f"status=$(git -C {root_value}/$project status --short) || exit $?; "
        'if [ -n "$status" ]; then '
        'printf \'%s:\\n%s\\n\' "$project" "$status"; fi; '
        "done"
    )


def user_systemctl_command(arguments: str) -> str:
    return user_session_command(f"systemctl --user {arguments}")


def showco_service_status_command(service: str, root: Path) -> str:
    return user_session_command(
        f'cd {shlex.quote(str(root / "showco"))} && PATH="$HOME/.local/bin:$PATH" '
        f"uv run --locked showco run service-status {service}"
    )


def showco_twitcho_health_command(root: Path) -> str:
    showco_directory = shlex.quote(str(root / "showco"))
    return user_session_command(
        f'cd {showco_directory} && PATH="$HOME/.local/bin:$PATH" '
        "uv run --locked showco run twitcho-health"
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
            verify_mixer_audio_inputs(
                provision_config, mixer.name, mixer.audio_device_names
            )
            for mixer in provision_config.mixers
            if mixer.audio_device_names
        ),
        *(
            verify_mixer_midi_input(provision_config, mixer.name, name)
            for mixer in provision_config.mixers
            for name in mixer.midi_input_names
        ),
    ]


def verify_mixer_audio_inputs(
    provision_config: config.Config, mixer_name: str, selectors: list[str]
) -> VerificationResult:
    selector_options = " ".join(f"-e {shlex.quote(s)}" for s in selectors)
    selector_names = "/".join(selectors)
    command = f"arecord -l | grep -Fi {selector_options} >/dev/null"
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
            name=f"{mixer_name} USB audio",
            error="",
            note=f"detection timed out after {ssh.SSH_VERIFICATION_TIMEOUT_SECONDS}s",
        )
    if completed.returncode == 0:
        return VerificationResult(name=f"{mixer_name} USB audio", error="")
    return VerificationResult(
        name=f"{mixer_name} USB audio",
        error="",
        note=f"{selector_names} not detected",
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
            "&& uv run --locked python -c",
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
