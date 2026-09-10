from __future__ import annotations

import unittest

from showco.runtime import models, readiness


class ReadinessTests(unittest.TestCase):
    def test_ready_when_required_services_and_disk_are_available(self) -> None:
        value = readiness.status(show_status())

        self.assertTrue(value.ready)
        self.assertEqual(
            [check.name for check in value.checks],
            ["Recs", "Recording", "Recording disk", "Lyte", "Streamo"],
        )

    def test_paused_recording_blocks_readiness(self) -> None:
        value = readiness.status(show_status(paused=True))

        self.assertFalse(value.ready)
        self.assertIn(
            models.ReadinessCheck(
                name="Recording", ok=False, message="recording is paused"
            ),
            value.checks,
        )

    def test_enabled_service_and_mixer_must_be_connected(self) -> None:
        value = readiness.status(
            show_status(
                lyte=models.LyteStatus(
                    service=models.ServiceStatus(name="lyte", state="offline")
                ),
                mixers=[models.MixerStatus(name="X18", state="waiting")],
            )
        )

        self.assertFalse(value.ready)
        self.assertIn(
            models.ReadinessCheck(name="X18", ok=False, message="waiting"),
            value.checks,
        )
        self.assertIn(
            models.ReadinessCheck(name="Lyte", ok=False, message="offline"),
            value.checks,
        )


def show_status(
    *,
    paused: bool = False,
    lyte: models.LyteStatus | None = None,
    mixers: list[models.MixerStatus] | None = None,
) -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name="recs", state="connected"),
            recording=True,
            paused=paused,
            disk=models.RecordingDiskStatus(
                path="/recordings", used_bytes=1, free_bytes=2, total_bytes=3
            ),
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name="streamo", state="disabled")
        ),
        lyte=(
            lyte
            or models.LyteStatus(
                service=models.ServiceStatus(name="lyte", state="disabled")
            )
        ),
        mixers=mixers or [],
    )
