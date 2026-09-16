from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import ActionResult


class Song(BaseModel, frozen=True):
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default='', max_length=1000)
    seconds: int = Field(default=0, ge=0, le=86400)


class Cue(BaseModel, frozen=True):
    label: str
    current: int
    next_index: int
    interval: bool = False


class SetList(BaseModel, frozen=True):
    revision: int = 0
    songs: list[Song] = Field(default_factory=list, max_length=200)
    current: int = -1
    next_index: int = 0
    interval: bool = False
    started_at: datetime | None = None
    pending: Cue | None = None
    message: str = ''


class SetListController:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.state = SetList()
        if path is not None:
            try:
                self.state = SetList.model_validate_json(path.read_text())
            except FileNotFoundError:
                pass

    def save(self, state: SetList) -> None:
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
        marker: Callable[[str], ActionResult],
        songs: list[Song] | None = None,
    ) -> ActionResult:
        if revision != self.state.revision:
            raise ValueError('Set list changed; refresh before submitting another cue')
        state = self.state
        if action == 'setlist-save':
            if state.pending:
                raise ValueError(
                    'Resolve the uncertain marker before replacing the set list'
                )
            self.save(
                SetList(
                    revision=revision + 1,
                    songs=songs or [],
                    message='Set list saved; position reset',
                )
            )
            return ActionResult(ok=True, message=self.state.message)
        if state.pending is not None:
            if action == 'setlist-accept':
                return self.finish('Cue kept by operator; marker delivery unverified')
            if action != 'setlist-retry':
                raise ValueError(
                    'Marker outcome uncertain. Keep the cue without resending, '
                    'or explicitly retry with duplicate risk.'
                )
            cue = state.pending
        elif action == 'setlist-skip':
            if state.next_index >= len(state.songs):
                raise ValueError('No next song to skip')
            self.save(
                state.model_copy(
                    update={
                        'revision': revision + 1,
                        'next_index': state.next_index + 1,
                        'message': 'Skipped next song; existing markers unchanged',
                    }
                )
            )
            return ActionResult(ok=True, message=self.state.message)
        elif action == 'setlist-interval':
            cue = Cue(
                label='interval',
                current=state.current,
                next_index=state.next_index,
                interval=True,
            )
        elif action in {'setlist-next', 'setlist-repeat'}:
            index = state.next_index if action == 'setlist-next' else state.current
            if not 0 <= index < len(state.songs):
                raise ValueError('No song available for this cue')
            cue = Cue(
                label=f'song start: {state.songs[index].title}',
                current=index,
                next_index=index + 1 if action == 'setlist-next' else state.next_index,
            )
        else:
            raise ValueError('Unknown set-list action')
        self.save(
            state.model_copy(
                update={
                    'revision': revision + 1,
                    'pending': cue,
                    'message': 'Marker pending; check its outcome before resending',
                }
            )
        )
        result = marker(cue.label)
        if not result.ok:
            self.save(
                self.state.model_copy(
                    update={'message': f'Marker unconfirmed: {result.message}'}
                )
            )
            return ActionResult(ok=False, message=self.state.message)
        return self.finish(f'Cue sent: {cue.label}')

    def finish(self, message: str) -> ActionResult:
        cue = self.state.pending
        if cue is None:
            raise ValueError('No pending cue')
        self.save(
            self.state.model_copy(
                update={
                    'revision': self.state.revision + 1,
                    'current': cue.current,
                    'next_index': cue.next_index,
                    'interval': cue.interval,
                    'pending': None,
                    'started_at': self.state.started_at or datetime.now().astimezone(),
                    'message': message,
                }
            )
        )
        return ActionResult(ok=True, message=message)
