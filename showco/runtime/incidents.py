from __future__ import annotations

from datetime import datetime

from . import models


class IncidentTimeline:
    def __init__(self) -> None:
        self.previous: dict[str, str] | None = None
        self.incidents: list[models.Incident] = []

    def observe(self, status: models.ShowStatus) -> list[models.Incident]:
        current = states(status)
        if self.previous is not None:
            for name, value in current.items():
                if self.previous.get(name) != value:
                    previous = self.previous.get(name, "unavailable")
                    self.incidents.insert(
                        0,
                        models.Incident(
                            timestamp=datetime.now().astimezone(),
                            message=f"{name}: {previous} to {value}",
                        ),
                    )
            self.incidents = self.incidents[:100]
        self.previous = current
        return list(self.incidents)


def states(status: models.ShowStatus) -> dict[str, str]:
    disk = status.recs.disk
    recording = (
        "paused"
        if status.recs.paused
        else "recording"
        if status.recs.recording
        else "stopped"
    )
    disk_state = (
        "unavailable"
        if disk is None
        else "paused"
        if disk.paused_for_disk_space
        else "alert"
        if disk.alert_active
        else "ready"
    )
    return {
        "Recs": status.recs.service.state,
        "Recording": recording,
        "Recording disk": disk_state,
        "Lyte": status.lyte.service.state,
        "Twitcho": status.twitcho.service.state,
        **{mixer.name: mixer.state for mixer in status.mixers},
    }
