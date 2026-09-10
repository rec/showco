from __future__ import annotations

from . import models


def status(value: models.ShowStatus) -> models.ReadinessStatus:
    checks = [
        _service_check("Recs", value.recs.service),
        models.ReadinessCheck(
            name="Recording",
            ok=value.recs.recording and not value.recs.paused,
            message=(
                "recording"
                if value.recs.recording and not value.recs.paused
                else "recording is paused"
                if value.recs.paused
                else "not recording"
            ),
        ),
        _disk_check(value.recs),
        *[_mixer_check(mixer) for mixer in value.mixers],
        _optional_service_check("Lyte", value.lyte.service),
        _optional_service_check("Twitcho", value.twitcho.service),
    ]
    return models.ReadinessStatus(
        checks=checks,
        ready=all(check.ok for check in checks),
    )


def _service_check(name: str, service: models.ServiceStatus) -> models.ReadinessCheck:
    return models.ReadinessCheck(
        name=name,
        ok=service.state == "connected",
        message=service.last_error or service.state,
    )


def _disk_check(recs: models.RecsStatus) -> models.ReadinessCheck:
    disk = recs.disk
    if disk is None:
        return models.ReadinessCheck(
            name="Recording disk",
            ok=False,
            message=recs.disk_error or recs.snapshot_error or "unavailable",
        )
    if disk.paused_for_disk_space:
        return models.ReadinessCheck(
            name="Recording disk", ok=False, message="recording paused for disk space"
        )
    if disk.alert_active:
        return models.ReadinessCheck(
            name="Recording disk", ok=False, message="disk-space alert"
        )
    return models.ReadinessCheck(name="Recording disk", ok=True, message="ready")


def _mixer_check(mixer: models.MixerStatus) -> models.ReadinessCheck:
    return models.ReadinessCheck(
        name=mixer.name,
        ok=mixer.state == "connected",
        message=mixer.error or mixer.state,
    )


def _optional_service_check(
    name: str, service: models.ServiceStatus
) -> models.ReadinessCheck:
    if service.state == "disabled":
        return models.ReadinessCheck(name=name, ok=True, message="disabled")
    return _service_check(name, service)
