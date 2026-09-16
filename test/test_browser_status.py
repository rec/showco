import subprocess
from pathlib import Path

import pytest

from showco.runtime import models


@pytest.mark.parametrize('state', ['connected', 'disabled'])
def test_browser_refresh_consumes_current_status_schema(state: str) -> None:
    status = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='offline')
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state=state),
            output_bitrate_kbps=128 if state == 'connected' else None,
        ),
        system=models.SystemStatus(temperature_c=42),
        lyte=models.LyteStatus(
            service=models.ServiceStatus(name='lyte', state=state),
            running=True,
            strings={
                'stage': models.LyteStringStatus(state='streaming', frame_count=42)
            },
        ),
    )
    subprocess.run(
        ['node', 'test/browser_status.cjs'],
        input=status.model_dump_json(),
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
