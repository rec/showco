import hashlib
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from . import lighting, models, recovery, setlist, soundcheck
from .lyte import LyteClient
from .recs import RecsClient


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


def refresh_soundcheck(
    controller: soundcheck.Soundcheck,
    show: models.ShowStatus,
    revision: str | None,
) -> None:
    settings = Path.home() / '.config/recs/settings.json'
    try:
        configuration = settings.read_bytes()
    except FileNotFoundError:
        configuration = b''
    controller.observe(
        show, hashlib.sha256(configuration).hexdigest() + (revision or '')
    )


def run_lighting(
    controller: lighting.LightingController,
    lyte: LyteClient | None,
    form: dict[str, str],
) -> models.ActionResult:
    action = form['action']
    if (
        action in {'lighting-accept', 'lighting-cancel', 'lighting-retry'}
        and form.get('confirmation') != 'resolve'
    ):
        raise ValueError('Confirm how to resolve the uncertain lighting cue first')
    if lyte is None or not lyte.enabled:
        raise ValueError('lyte is disabled')
    cues = (
        TypeAdapter(list[lighting.LightingCue]).validate_json(form.get('cues', '[]'))
        if action == 'lighting-save'
        else None
    )
    return controller.act(
        action, int(form.get('revision', '-1')), lyte.select_animation, cues
    )


def run_setlist(
    controller: setlist.SetListController, recs: RecsClient, form: dict[str, str]
) -> models.ActionResult:
    action = form['action']
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
    return controller.act(
        action,
        int(form.get('revision', '-1')),
        lambda label: recs.action('mark', label=label),
        songs,
    )


def run_recovery(
    controller: recovery.Recovery,
    form: dict[str, str],
    show: models.ShowStatus,
    refresh: Callable[[], models.ShowStatus],
    restart: Callable[[str], models.ActionResult],
    remember: Callable[[str], None],
    invalidate_soundcheck: Callable[[], None],
) -> models.ActionResult:
    action = form['action']
    if action == 'recovery-refresh':
        return controller.verify(refresh(), remember)
    if action == 'recovery-restart':
        name = form.get('service', '')
        if form.get('confirmation') != name:
            raise ValueError('Confirm the named service interruption before restarting')
        invalidate_soundcheck()
        return controller.restart(
            name, int(form.get('revision', '-1')), show, restart, remember
        )
    raise ValueError('Unknown recovery action')


def run_soundcheck(
    controller: soundcheck.Soundcheck,
    recs: RecsClient,
    lyte: LyteClient | None,
    form: dict[str, str],
    show: models.ShowStatus,
) -> models.ActionResult:
    action = form['action']
    if form.get('scope') != controller.state.scope:
        raise ValueError('Soundcheck conditions changed; refresh before checking again')
    if action == 'soundcheck-begin':
        expected = TypeAdapter(list[str]).validate_json(form.get('expected', '[]'))
        if not set(expected) <= {soundcheck.input_key(c) for c in show.recs.channels}:
            raise ValueError('Selected inputs are no longer available')
        controller.begin(expected)
        return models.ActionResult(
            ok=True, message='Soundcheck started; recording has not changed'
        )
    if action == 'soundcheck-check':
        return controller.check(
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
        if not controller.state.expected_inputs:
            raise ValueError('Begin soundcheck first')
        if action == 'soundcheck-lights':
            if lyte is None:
                raise ValueError('lyte is disabled')
            return lyte.test()
        if action == 'soundcheck-start-recording':
            controller.record_start(show)
            result = recs.action('resume_recording')
        else:
            result = recs.action('pause_recording')
        recs.snapshot_client.invalidate()
        return result
    raise ValueError('Unknown soundcheck action')
