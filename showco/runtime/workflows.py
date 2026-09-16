import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter

from ..deployment import bundle
from . import lighting, models, recovery, setlist, soundcheck

if TYPE_CHECKING:
    from .server import ShowcoApp, ShowcoHandler


class ExpectedInput(BaseModel, frozen=True):
    key: str
    name: str


class WorkflowStatus(BaseModel, frozen=True):
    show: models.ShowStatus
    lighting: lighting.LightingState
    setlist: setlist.SetList
    soundcheck: soundcheck.SoundcheckState
    recovery: recovery.RecoveryState
    inputs: list[ExpectedInput]
    soundcheck_error: str | None = None


def status(app: 'ShowcoApp') -> WorkflowStatus:
    show = app.status()
    return WorkflowStatus(
        show=show,
        setlist=app.setlist.state,
        lighting=app.lighting.state,
        soundcheck=app.soundcheck.state,
        recovery=app.recovery.state,
        soundcheck_error=app.soundcheck_error,
        inputs=[
            ExpectedInput(key=soundcheck.input_key(c), name=c.name)
            for c in show.recs.channels
        ],
    )


def refresh_soundcheck(app: 'ShowcoApp', show: models.ShowStatus) -> None:
    settings = Path.home() / '.config/recs/settings.json'
    try:
        configuration = settings.read_bytes()
    except FileNotFoundError:
        configuration = b''
    app.soundcheck.observe(
        show, hashlib.sha256(configuration).hexdigest() + (app.revision or '')
    )


def run(app: 'ShowcoApp', form: dict[str, str]) -> models.ActionResult:
    action = form['action']
    if action.startswith('lighting-'):
        if (
            action in {'lighting-accept', 'lighting-cancel', 'lighting-retry'}
            and form.get('confirmation') != 'resolve'
        ):
            raise ValueError('Confirm how to resolve the uncertain lighting cue first')
        if app.lyte is None or not app.lyte.enabled:
            raise ValueError('lyte is disabled')
        cues = (
            TypeAdapter(list[lighting.LightingCue]).validate_json(
                form.get('cues', '[]')
            )
            if action == 'lighting-save'
            else None
        )
        return app.lighting.act(
            action, int(form.get('revision', '-1')), app.lyte.select_animation, cues
        )
    if action.startswith('setlist-'):
        if (
            action in {'setlist-retry', 'setlist-accept'}
            and form.get('confirmation') != 'resolve'
        ):
            raise ValueError('Confirm how to resolve the uncertain marker first')
        songs = (
            TypeAdapter(list[setlist.Song]).validate_json(form.get('songs', '[]'))
            if action == 'setlist-save'
            else None
        )
        return app.setlist.act(
            action,
            int(form.get('revision', '-1')),
            lambda label: app.recs.action('mark', label=label),
            songs,
        )
    show = app.status()
    if action.startswith('recovery-'):

        def remember(message: str) -> None:
            with app.status_lock:
                app.incidents.record(message)

        if action == 'recovery-refresh':
            app.recs.snapshot_client.invalidate()
            return app.recovery.verify(app.status(), remember)
        if action == 'recovery-restart':
            name = form.get('service', '')
            if form.get('confirmation') != name:
                raise ValueError(
                    'Confirm the named service interruption before restarting'
                )
            with app.soundcheck_lock:
                app.soundcheck.invalidate(
                    'Service restart requested; repeat soundcheck'
                )
            result = app.recovery.restart(
                name,
                int(form.get('revision', '-1')),
                show,
                app.recovery_restart,
                remember,
            )
            app.recs.snapshot_client.invalidate()
            return result
        raise ValueError('Unknown recovery action')
    with app.soundcheck_lock:
        if app.soundcheck_error:
            raise ValueError(app.soundcheck_error)
        return run_soundcheck(app, form, show)


def run_soundcheck(
    app: 'ShowcoApp', form: dict[str, str], show: models.ShowStatus
) -> models.ActionResult:
    action = form['action']
    if form.get('scope') != app.soundcheck.state.scope:
        raise ValueError('Soundcheck conditions changed; refresh before checking again')
    if action == 'soundcheck-begin':
        expected = TypeAdapter(list[str]).validate_json(form.get('expected', '[]'))
        if not set(expected) <= {soundcheck.input_key(c) for c in show.recs.channels}:
            raise ValueError('Selected inputs are no longer available')
        app.soundcheck.begin(expected)
        return models.ActionResult(
            ok=True, message='Soundcheck started; recording has not changed'
        )
    if action == 'soundcheck-check':
        return app.soundcheck.check(
            form.get('step', ''),
            show,
            form.get('confirmation') == 'observed',
            form.get('skip') == 'yes',
            form.get('note', ''),
        )
    if action in {
        'soundcheck-start-recording',
        'soundcheck-pause-recording',
        'soundcheck-lights',
    }:
        if form.get('confirmation') != 'output':
            raise ValueError('Confirm the recording or output change first')
        if not app.soundcheck.state.expected_inputs:
            raise ValueError('Begin soundcheck first')
        if action == 'soundcheck-lights':
            if app.lyte is None:
                raise ValueError('lyte is disabled')
            return app.lyte.test()
        if action == 'soundcheck-start-recording':
            app.soundcheck.record_start(show)
            result = app.recs.action('resume_recording')
        else:
            result = app.recs.action('pause_recording')
        app.recs.snapshot_client.invalidate()
        return result
    raise ValueError('Unknown soundcheck action')


def download(handler: 'ShowcoHandler') -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        destination = bundle.create_bundle(root / 'bundle')
        archive = Path(
            shutil.make_archive(str(root / 'diagnostics'), 'gztar', destination)
        )
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/gzip')
        handler.send_header(
            'Content-Disposition', 'attachment; filename="showco-diagnostics.tar.gz"'
        )
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Content-Length', str(archive.stat().st_size))
        handler.end_headers()
        with archive.open('rb') as source:
            shutil.copyfileobj(source, handler.wfile, length=65536)
