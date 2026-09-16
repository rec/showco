import subprocess
from pathlib import Path

from showco.runtime import lighting, models, recovery, setlist, soundcheck, workflows


def test_workflow_browser_cues_uncertainty_checks_recovery_and_disconnection() -> None:
    payload = workflows.WorkflowStatus(
        show=models.ShowStatus(
            recs=models.RecsStatus(
                service=models.ServiceStatus(name='recs', state='connected')
            ),
            streamo=models.StreamoStatus(
                service=models.ServiceStatus(name='streamo', state='disabled')
            ),
        ),
        lighting=lighting.LightingState(),
        setlist=setlist.SetList(
            revision=1,
            songs=[
                setlist.Song(title='Opening', notes='<script>private notes</script>'),
                setlist.Song(title='Second'),
            ],
        ),
        soundcheck=soundcheck.SoundcheckState(),
        recovery=recovery.RecoveryState(),
        inputs=[],
    )
    subprocess.run(
        ['node', 'test/browser_workflows.cjs'],
        input=payload.model_dump_json(),
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
