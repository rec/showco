"""Manual lighting cues owned by showCo; lyte owns look execution."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from .models import ActionResult


class LightingCue(BaseModel, frozen=True):
    name: str = Field(min_length=1, max_length=200)
    animation: str = Field(min_length=1, max_length=200)


class LightingState(BaseModel, frozen=True):
    revision: int = 0
    cues: list[LightingCue] = Field(default_factory=list, max_length=200)
    current: int = -1
    pending: int | None = None
    message: str = ''


class LightingController:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.state = LightingState()
        if path is not None:
            try:
                self.state = LightingState.model_validate_json(path.read_text())
            except FileNotFoundError:
                pass

    def save(self, state: LightingState) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(state.model_dump_json())
            temporary.replace(self.path)
        self.state = state

    def act(
        self,
        action: str,
        revision: int,
        select: Callable[[str], ActionResult],
        cues: list[LightingCue] | None = None,
    ) -> ActionResult:
        state = self.state
        if revision != state.revision:
            raise ValueError('Lighting cues changed; refresh before submitting again')
        if state.pending is not None:
            if action == 'lighting-accept':
                self.save(
                    state.model_copy(
                        update={
                            'revision': revision + 1,
                            'current': state.pending,
                            'pending': None,
                            'message': (
                                'Cue kept by operator; lighting delivery unverified'
                            ),
                        }
                    )
                )
                return ActionResult(ok=True, message=self.state.message)
            if action == 'lighting-cancel':
                self.save(
                    state.model_copy(
                        update={
                            'revision': revision + 1,
                            'pending': None,
                            'message': 'Cue position kept; no lighting command sent',
                        }
                    )
                )
                return ActionResult(ok=True, message=self.state.message)
            if action != 'lighting-retry':
                raise ValueError(
                    'Lighting outcome uncertain; resolve the pending cue first'
                )
            index = state.pending
        elif action == 'lighting-save':
            self.save(
                LightingState(
                    revision=revision + 1,
                    cues=cues or [],
                    message=(
                        'Lighting cues saved; position reset without changing lights'
                    ),
                )
            )
            return ActionResult(ok=True, message=self.state.message)
        elif action in {'lighting-go', 'lighting-back'}:
            index = state.current + (1 if action == 'lighting-go' else -1)
            if not 0 <= index < len(state.cues):
                raise ValueError(
                    'No next cue' if action == 'lighting-go' else 'No previous cue'
                )
        else:
            raise ValueError('Unknown lighting cue action')
        self.save(
            state.model_copy(
                update={
                    'revision': revision + 1,
                    'pending': index,
                    'message': (
                        'Lighting selection pending; '
                        'inspect live state before resolving'
                    ),
                }
            )
        )
        result = select(state.cues[index].animation)
        # A failed or lost acknowledgement cannot prove that lyte did not select it.
        if not result.ok:
            self.save(
                self.state.model_copy(
                    update={'message': f'Lighting unconfirmed: {result.message}'}
                )
            )
            return ActionResult(ok=False, message=self.state.message)
        self.save(
            self.state.model_copy(
                update={
                    'revision': self.state.revision + 1,
                    'current': index,
                    'pending': None,
                    'message': (
                        f'Cue queued: {state.cues[index].name}. Check live lyte state.'
                    ),
                }
            )
        )
        return ActionResult(ok=True, message=self.state.message)
