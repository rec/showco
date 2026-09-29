from __future__ import annotations

import math

from . import models, recs_snapshot


def track_channel(
    device: str, channel: str, track_names: dict[str, dict[str, int]]
) -> int | None:
    first, _, _ = channel.partition('-')
    if first.isdigit():
        return int(first)
    value = track_names.get(device, {}).get(channel)
    if isinstance(value, int):
        return value
    return None


def replace_track_name(
    track_names: dict[str, dict[str, int]],
    device: str,
    channel: int,
    track_name: str,
) -> dict[str, dict[str, int]]:
    updated = {k: dict(v) for k, v in track_names.items()}
    names = updated.setdefault(device, {})
    for name, value in list(names.items()):
        if value == channel:
            del names[name]
    if track_name:
        names[track_name] = channel
    return updated


def track_names_response(value: object) -> dict[str, dict[str, int]] | None:
    if not recs_snapshot.object_dict(value) or value.get('type') != 'track_names':
        return None
    raw = value.get('track_names')
    if not recs_snapshot.object_dict(raw):
        return None
    result: dict[str, dict[str, int]] = {}
    for device, names in raw.items():
        if not recs_snapshot.object_dict(names):
            return None
        if any(not isinstance(v, int) or isinstance(v, bool) for v in names.values()):
            return None
        result[device] = {k: v for k, v in names.items() if isinstance(v, int)}
    return result


def channel_levels(rows: list[dict[str, object]]) -> list[models.ChannelLevel]:
    channels = []
    device = ''
    for row in rows:
        if isinstance(name := row.get('device'), str):
            device = name
        if not isinstance(name := row.get('channel'), str):
            continue
        signal = _float(row.get('signal'))
        channels.append(
            models.ChannelLevel(
                name=name,
                state=level_state(signal),
                device=device,
                channels=_channels(row.get('channels')),
                signal=signal,
                on=row.get('on') is True,
            )
        )
    return channels


def level_state(signal: float | None) -> str:
    if signal is None or signal < 0.001:
        return 'silent'
    if signal < 1 / 3:
        return 'present'
    if signal < 0.9:
        return 'healthy'
    return 'clipping'


def stereo_tracks(
    channels: list[models.ChannelLevel], device: str, selected: list[int]
) -> list[list[int]] | models.ActionResult:
    source_tracks = [
        channel.channels for channel in channels if channel.device == device
    ]
    if selected not in source_tracks:
        return models.ActionResult(
            ok=False, message='recs channel is no longer available'
        )
    if len(selected) == 2:
        tracks: list[list[int]] = []
        for track in source_tracks:
            if track == selected:
                tracks.extend([[selected[0]], [selected[1]]])
            else:
                tracks.append(track)
        return tracks
    if len(selected) != 1:
        return models.ActionResult(ok=False, message='recs channel layout is invalid')
    right = [selected[0] + 1]
    if right not in source_tracks:
        return models.ActionResult(
            ok=False, message='recs channel cannot be paired with its right neighbor'
        )
    tracks = []
    for track in source_tracks:
        if track == selected:
            tracks.append(selected + right)
        elif track != right:
            tracks.append(track)
    return tracks


def track_name(
    track_names: dict[str, dict[str, int]], device: str, channel: int
) -> str:
    for name, first_channel in track_names.get(device, {}).items():
        if first_channel == channel:
            return name
    return ''


def _float(value: object) -> float | None:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None


def _channels(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    channels: list[int] = []
    for channel in value:
        if not isinstance(channel, int):
            return []
        channels.append(channel)
    return channels
