from pathlib import Path
from uuid import uuid4


def quarantine(path: Path, error: Exception) -> str:
    backup = path.with_name(f'{path.stem}.ERROR-{uuid4().hex}{path.suffix}')
    try:
        path.rename(backup)
    except OSError as rename_error:
        raise OSError(
            f'Cannot read {path} or preserve it as {backup}: {rename_error}'
        ) from rename_error
    return f'Cannot read {path}: {error}. Preserved as {backup}; starting empty.'
