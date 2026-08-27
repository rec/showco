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
from typing_extensions import TypeIs

from . import machine_role
from .provision import config

PROVISION_DIR = Path(__file__).resolve().parent / "provision"
MAX_CARD_SIZE = 256 * 1024**3
USER_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_-]*")


class PrepareCardOptions(BaseModel, frozen=True):
    card: Path | None = None
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
    card_path = select_card(options.card)
    path = card_user_data_path(card_path)
    show_card(card_path)
    prompt = f"Prepare {path.parent} on {card_path}? Type yes to continue: "
    if input(prompt).casefold() != "yes":
        sys.exit("Cancelled.")
    changed = prepare_card(path.parent, user)
    if changed:
        print(f"Prepared {path} for passwordless sudo as {user}.")
    else:
        print(f"{path} is already prepared for passwordless sudo as {user}.")
    print(f"Ejecting {card_path}; remove the card after macOS confirms.")
    eject_card(card_path)
    return 0


def prepare_card(boot: Path, user: str) -> bool:
    if USER_NAME_PATTERN.fullmatch(user) is None:
        sys.exit(f"ERROR: invalid Linux user name: {user!r}")
    path = boot / "user-data"
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


def select_card(card: Path | None) -> Path:
    cards = external_cards()
    if card is not None:
        if card in cards:
            return card
        sys.exit(f"ERROR: card is not an external physical disk: {card}")
    if len(cards) == 1:
        return cards[0]
    if not cards:
        sys.exit("ERROR: no external physical cards of 256 GiB or smaller found")
    names = ", ".join(str(value) for value in cards)
    sys.exit(f"ERROR: multiple external physical cards found: {names}; pass --card")


def card_user_data_path(card: Path) -> Path:
    for value in disk_values():
        device = value.get("DeviceIdentifier")
        if not isinstance(device, str) or Path("/dev") / device != card:
            continue
        if path := mounted_user_data_path(value):
            return path
    sys.exit(f"ERROR: no mounted Raspberry Pi boot volume found on {card}")


def disk_values() -> list[dict[str, object]]:
    result = subprocess.run(
        ["diskutil", "list", "-plist", "external", "physical"],
        capture_output=True,
        check=True,
    )
    values = plistlib.loads(result.stdout)
    disks = values.get("AllDisksAndPartitions", [])
    return [value for value in disks if isinstance(value, dict)]


def external_cards() -> list[Path]:
    cards = []
    for value in disk_values():
        device = value.get("DeviceIdentifier")
        size = value.get("Size")
        if isinstance(device, str) and isinstance(size, int) and size <= MAX_CARD_SIZE:
            cards.append(Path("/dev") / device)
    return cards


def show_card(card: Path) -> None:
    subprocess.run(["diskutil", "list", str(card)], check=True)


def eject_card(card: Path) -> None:
    subprocess.run(["diskutil", "eject", str(card)], check=True)


def mounted_user_data_path(value: dict[str, object]) -> Path | None:
    partitions = value.get("Partitions", [])
    if not isinstance(partitions, list):
        return None
    for partition in partitions:
        if not is_dictionary(partition):
            continue
        mount_point = partition.get("MountPoint")
        if not isinstance(mount_point, str):
            continue
        if (path := Path(mount_point) / "user-data").is_file():
            return path
    return None


def is_dictionary(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def configured_user(config_path: Path) -> str:
    values = config.read_toml(config_path)
    network = config.table_value(values, "network")
    if user := config.string_value(network, "user", default=os.environ.get("USER", "")):
        return user
    sys.exit("ERROR: set network.user in the provisioning configuration or pass --user")
