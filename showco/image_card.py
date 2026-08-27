from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import tyro
from pydantic import BaseModel

from . import card, machine_role
from .provision import config

DEFAULT_IMAGE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-06-19/2026-06-18-raspios-trixie-arm64-lite.img.xz"
DEFAULT_IMAGE_SHA256 = (
    "e235fd24fc5f039c08daba7d3abc04aecc7313f979d16d2a3fdad29dd44c33a9"
)
DEFAULT_IMAGER = Path("/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager")
PROVISION_DIR = Path(__file__).resolve().parent / "provision"


class ImageCardOptions(BaseModel, frozen=True):
    device: Path
    yes: Annotated[bool, tyro.conf.arg(aliases=["-y"])] = False
    boot: Path = Path("/Volumes/bootfs")
    image_url: str = DEFAULT_IMAGE_URL
    image_sha256: str = DEFAULT_IMAGE_SHA256
    imager: Path = DEFAULT_IMAGER
    ssh_key: Path = Path.home() / ".ssh/id_ed25519.pub"
    secrets: Path = PROVISION_DIR / "secrets.toml"


def main(argv: list[str] | None = None) -> int:
    machine_role.require_provisioning_machine("showco image-card")
    options = tyro.cli(
        ImageCardOptions,
        args=argv,
        description="Image a Showco Raspberry Pi SD card",
    )
    return run(options)


def run(options: ImageCardOptions) -> int:
    if not options.device.name.startswith("disk"):
        sys.exit("ERROR: device must be a macOS disk such as /dev/disk4")
    if not options.imager.is_file():
        sys.exit(f"ERROR: Raspberry Pi Imager not found: {options.imager}")
    confirm_device(options.device, options.yes)
    values = config.merge_values(
        config.read_toml(PROVISION_DIR / "config.toml"),
        config.read_toml(options.secrets),
    )
    provision_config = config.config_from_values(values)
    try:
        subprocess.run(
            [
                str(options.imager),
                "--cli",
                "--debug",
                "--sha256",
                options.image_sha256,
                options.image_url,
                str(options.device),
            ],
            check=True,
        )
    except KeyboardInterrupt:
        sys.exit("Interrupted. The card may be partially written; reimage it.")
    subprocess.run(["diskutil", "mountDisk", str(options.device)], check=True)
    card.write_cloud_init(
        options.boot,
        provision_config.network.host,
        provision_config.network.user,
        options.ssh_key.expanduser().read_text(),
        config.external_wifi(provision_config),
    )
    print(f"Imaged {options.device} and wrote cloud-init to {options.boot}.")
    return 0


def confirm_device(device: Path, yes: bool) -> None:
    subprocess.run(["diskutil", "list", str(device)], check=True)
    if yes:
        return
    if input(f"Erase {device}? Type yes to continue: ").casefold() != "yes":
        sys.exit("Cancelled.")
