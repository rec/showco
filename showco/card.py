from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import tyro
from pydantic import BaseModel

from . import machine_role
from .provision import config

PROVISION_DIR = Path(__file__).resolve().parent / "provision"
USER_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_-]*")


class PrepareCardOptions(BaseModel, frozen=True):
    boot: Path = Path("/Volumes/bootfs")
    config_path: Annotated[Path, tyro.conf.arg(name="config")] = (
        PROVISION_DIR / "config.toml"
    )
    user: str | None = None


def main(argv: list[str] | None = None) -> int:
    machine_role.require_provisioning_machine("showco prepare-card")
    options = tyro.cli(
        PrepareCardOptions,
        args=argv,
        description="Prepare an Imager-written Raspberry Pi OS card for Showco",
    )
    user = options.user or configured_user(options.config_path)
    changed = prepare_card(options.boot, user)
    path = options.boot / "user-data"
    if changed:
        print(f"Prepared {path} for passwordless sudo as {user}.")
    else:
        print(f"{path} is already prepared for passwordless sudo as {user}.")
    return 0


def prepare_card(boot: Path, user: str) -> bool:
    if USER_NAME_PATTERN.fullmatch(user) is None:
        sys.exit(f"ERROR: invalid Linux user name: {user!r}")
    path = boot / "user-data"
    if not path.is_file():
        mount_external_disks()
    if not path.is_file():
        sys.exit(f"ERROR: Raspberry Pi Imager user-data file not found: {path}")

    contents = path.read_text()
    if not contents.lstrip().startswith("#cloud-config"):
        sys.exit(f"ERROR: {path} is not a cloud-init user-data file")

    sudoers_path = f"/etc/sudoers.d/010_{user}-nopasswd"
    if sudoers_path in contents:
        return False

    commands = [
        f"  - [ sh, -c, \"echo '{user} ALL=(ALL) NOPASSWD: ALL' > {sudoers_path}\" ]\n",
        f'  - [ chmod, "0440", "{sudoers_path}" ]\n',
    ]
    lines = contents.splitlines(keepends=True)
    runcmd_index = next(
        (i for i, line in enumerate(lines) if line == "runcmd:\n"), None
    )
    if runcmd_index is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(["runcmd:\n", *commands])
    else:
        insert_index = runcmd_index + 1
        while insert_index < len(lines):
            line = lines[insert_index]
            if line and not line.startswith((" ", "\t", "#", "\n")):
                break
            insert_index += 1
        lines[insert_index:insert_index] = commands
    path.write_text("".join(lines))
    return True


def write_cloud_init(
    boot: Path,
    host: str,
    user: str,
    ssh_public_key: str,
    external_network: config.Network,
) -> None:
    if USER_NAME_PATTERN.fullmatch(user) is None:
        sys.exit(f"ERROR: invalid Linux user name: {user!r}")
    key = ssh_public_key.strip()
    if not key.startswith("ssh-"):
        sys.exit("ERROR: SSH public key must start with ssh-")
    (boot / "user-data").write_text(
        "#cloud-config\n"
        f"hostname: {yaml_string(host)}\n"
        "manage_etc_hosts: true\n"
        "users:\n"
        f"  - name: {yaml_string(user)}\n"
        "    groups: [adm, dialout, cdrom, sudo, audio, video, plugdev, users,\n"
        "      gpio, i2c, spi, render]\n"
        "    shell: /bin/bash\n"
        "    lock_passwd: true\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    ssh_authorized_keys:\n"
        f"      - {yaml_string(key)}\n"
        "ssh_pwauth: false\n"
        "rpi:\n"
        "  enable_ssh: true\n"
    )
    if external_network.name:
        (boot / "network-config").write_text(
            "version: 2\n"
            "wifis:\n"
            "  wlan0:\n"
            "    optional: true\n"
            "    dhcp4: true\n"
            "    access-points:\n"
            f"      {yaml_string(external_network.name)}:\n"
            f"        password: {yaml_string(external_network.password)}\n"
        )


def yaml_string(value: str) -> str:
    return json.dumps(value)


def mount_external_disks() -> None:
    print("Mounting external disks...")
    result = subprocess.run(
        ["diskutil", "list", "-plist", "external", "physical"],
        capture_output=True,
        check=True,
    )
    values = plistlib.loads(result.stdout)
    for disk in values.get("WholeDisks", []):
        if isinstance(disk, str):
            subprocess.run(["diskutil", "mountDisk", f"/dev/{disk}"], check=False)


def configured_user(config_path: Path) -> str:
    values = config.read_toml(config_path)
    network = config.table_value(values, "network")
    if user := config.string_value(network, "user", default=os.environ.get("USER", "")):
        return user
    sys.exit("ERROR: set network.user in the provisioning configuration or pass --user")
