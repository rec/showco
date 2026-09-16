from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from . import models


class RecoveryState(BaseModel, frozen=True):
    revision: int = 0
    service: str = ''
    original_failure: str = ''
    requested_at: datetime | None = None
    outcome: str = 'idle'
    message: str = ''


class Recovery:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.state = RecoveryState()
        if path is not None:
            try:
                self.state = RecoveryState.model_validate_json(path.read_text())
            except FileNotFoundError:
                pass

    def save(self, state: RecoveryState) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(state.model_dump_json())
            temporary.replace(self.path)
        self.state = state

    def restart(
        self,
        name: str,
        revision: int,
        status: models.ShowStatus,
        restart: Callable[[str], models.ActionResult],
        remember: Callable[[str], None],
    ) -> models.ActionResult:
        if revision != self.state.revision:
            raise ValueError(
                'Recovery request already handled or changed; '
                'refresh before choosing another action'
            )
        if name not in {'recs', 'lyte', 'streamo'}:
            raise ValueError('Unsupported recovery service')
        service = getattr(status, name).service
        if service.state in {'connected', 'disabled'}:
            raise ValueError(
                'Restart is offered only for a disconnected or failed enabled service'
            )
        if self.state.outcome == 'checking':
            raise ValueError(
                'Verify the previous recovery before requesting another restart'
            )
        failure = service.last_error or service.state
        state = RecoveryState(
            revision=revision + 1,
            service=name,
            original_failure=failure,
            requested_at=datetime.now().astimezone(),
            outcome='checking',
            message='Restart requested; outcome not yet verified. Do not resend.',
        )
        self.save(state)
        remember(f'{name} recovery requested; original failure: {failure}')
        result = restart(name)
        self.save(
            state.model_copy(
                update={
                    'message': result.message
                    if result.ok
                    else 'Restart failed or unconfirmed: ' + result.message,
                    'outcome': 'checking' if result.ok else 'failed',
                }
            )
        )
        remember(f'{name} recovery command: {self.state.message}')
        return result

    def verify(
        self, status: models.ShowStatus, remember: Callable[[str], None]
    ) -> models.ActionResult:
        state = self.state
        if not state.service:
            return models.ActionResult(
                ok=True, message='Status refreshed; no recovery request to verify'
            )
        service = getattr(status, state.service).service
        ok = service.state == 'connected'
        message = f'{state.service}: {service.last_error or service.state}; ' + (
            'service recovery verified; missing audio is not recovered'
            if ok
            else 'recovery not verified; inspect the service before explicitly retrying'
        )
        changed = (
            state.outcome != ('recovered' if ok else 'failed')
            or state.message != message
        )
        self.save(
            state.model_copy(
                update={'outcome': 'recovered' if ok else 'failed', 'message': message}
            )
        )
        if changed:
            remember(f'Recovery verification: {message}')
        return models.ActionResult(ok=ok, message=message)
