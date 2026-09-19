from pathlib import Path

from pydantic import BaseModel, ValidationError


class PerformanceState(BaseModel, frozen=True):
    locked: bool


class PerformanceLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.error: str | None = None
        self.locked = False
        if path is not None:
            try:
                self.locked = PerformanceState.model_validate_json(
                    path.read_text()
                ).locked
            except FileNotFoundError:
                pass
            except (OSError, ValidationError) as error:
                self.locked = True
                self.error = (
                    f'Cannot read performance lock; protected actions blocked: {error}'
                )

    def set(self, locked: bool) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(PerformanceState(locked=locked).model_dump_json())
            temporary.replace(self.path)
        self.locked = locked
        self.error = None


PROTECTED_ACTIONS = {
    'setlist-save',
    'lighting-save',
    'soundcheck-begin',
    'soundcheck-check',
    'soundcheck-start-recording',
    'soundcheck-pause-recording',
    'soundcheck-lights',
    'recovery-restart',
    'cable-test',
    'lyte-test',
    'recs-calibrate',
    'recs-shutdown',
    'recs-new-session',
    'recs-track-name',
    'recs-set-stereo',
    'recs-set-attr',
    'recs-set-noise-floor',
    'recs-key-label',
    'recs-reload-profiles',
    'music-setup',
    'music-record',
    'music-teardown',
    'music-stop',
}
