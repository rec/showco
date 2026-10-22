from __future__ import annotations

from . import models


def status(value: models.ShowStatus) -> models.ReadinessStatus:
    checks = [
        _service_check('recs', value.recs.service),
        models.ReadinessCheck(
            name='Recording',
            ok=value.recs.service.state == 'connected'
            and value.recs.snapshot_available
            and not value.recs.paused,
            message=(
                'recording state unavailable'
                if value.recs.service.state != 'connected'
                or not value.recs.snapshot_available
                else 'recording is paused'
                if value.recs.paused
                else 'recording enabled; check audio writes separately'
            ),
        ),
        _disk_check(value.recs),
        *[_mixer_check(mixer) for mixer in value.mixers],
        _optional_service_check('lyte', value.lyte.service),
        _optional_service_check('streamO', value.streamo.service),
    ]
    if value.network is not None:
        checks.insert(
            0,
            models.ReadinessCheck(
                name='External Wi-Fi',
                ok=value.network.connected,
                message=value.network.message,
            ),
        )
    return models.ReadinessStatus(
        checks=checks,
        ready=all(check.ok for check in checks),
    )


def input_checks(channels: list[models.ChannelLevel]) -> list[models.InputCheck]:
    return [
        models.InputCheck(
            name=f'{c.device} {c.name}'.strip(),
            ok=c.state in {'present', 'healthy'},
            message='signal present' if c.state in {'present', 'healthy'} else c.state,
        )
        for c in channels
        if c.on
    ]


def _service_check(name: str, service: models.ServiceStatus) -> models.ReadinessCheck:
    return models.ReadinessCheck(
        name=name,
        ok=service.state == 'connected',
        message=service.last_error or service.state,
    )


def _disk_check(recs: models.RecsStatus) -> models.ReadinessCheck:
    disk = recs.disk
    if disk is None:
        return models.ReadinessCheck(
            name='Recording disk',
            ok=False,
            message=recs.disk_error or recs.snapshot_error or 'unavailable',
        )
    if disk.paused_for_disk_space:
        return models.ReadinessCheck(
            name='Recording disk', ok=False, message='recording paused for disk space'
        )
    if disk.alert_active:
        return models.ReadinessCheck(
            name='Recording disk', ok=False, message='disk-space alert'
        )
    return models.ReadinessCheck(name='Recording disk', ok=True, message='ready')


def _mixer_check(mixer: models.MixerStatus) -> models.ReadinessCheck:
    return models.ReadinessCheck(
        name=mixer.name,
        ok=mixer.state == 'connected',
        message=mixer.error or mixer.state,
    )


def _optional_service_check(
    name: str, service: models.ServiceStatus
) -> models.ReadinessCheck:
    if service.state == 'disabled':
        return models.ReadinessCheck(name=name, ok=True, message='disabled')
    return _service_check(name, service)
