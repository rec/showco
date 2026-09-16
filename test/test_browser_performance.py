import subprocess
from pathlib import Path

from showco.runtime import models


def test_performance_browser_controls_pins_and_disconnection() -> None:
    status = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            recording=True,
            channels=[
                models.ChannelLevel(
                    name='Vocal', device='X18', channels=[1], state='healthy', on=True
                )
            ],
            disk=models.RecordingDiskStatus(
                path='/recordings', used_bytes=0, free_bytes=100, total_bytes=100
            ),
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
        performance_locked=True,
    )
    subprocess.run(
        ['node', 'test/browser_performance.cjs'],
        input=status.model_dump_json(),
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
