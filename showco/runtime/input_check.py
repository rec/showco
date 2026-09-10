from __future__ import annotations

from . import models


def checks(channels: list[models.ChannelLevel]) -> list[models.InputCheck]:
    return [
        models.InputCheck(
            name=f"{channel.device} {channel.name}".strip(),
            ok=channel.state != "silent",
            message="signal present" if channel.state != "silent" else "silent",
        )
        for channel in channels
        if channel.on
    ]
