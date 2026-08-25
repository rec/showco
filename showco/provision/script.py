from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import Path

from . import config

SCRIPT_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (SCRIPT_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()


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
            "networks.internal.wired.x18.ip_address", x18_network.ip_address
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
        "SHOWCO_REPO": provision_config.git.showco.url,
        "SHOWCO_REFNAME": provision_config.git.showco.refname,
        "LYTE_REPO": provision_config.git.lyte.url,
        "LYTE_REFNAME": provision_config.git.lyte.refname,
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
                n for mixer in provision_config.mixers for n in mixer.audio_device_names
            )
        ),
        "RECS_MIDI_INPUT_NAMES": "\n".join(
            unique_selectors(
                n for mixer in provision_config.mixers for n in mixer.midi_input_names
            )
        ),
        "RECS_OSC_NODES_TOML": osc_nodes_toml(provision_config.mixers),
    }
    assignments = [f"{key}={shlex.quote(value)}" for key, value in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def shell_bool(value: bool) -> str:
    return "true" if value else "false"


def mixers_toml(mixers: list[config.MixerSpec]) -> str:
    lines: list[str] = []
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
    lines: list[str] = []
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
