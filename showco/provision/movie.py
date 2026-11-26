"""Build and cache the validated movie on the provisioning machine."""

import hashlib
import os
from glob import glob
from math import isfinite
from pathlib import Path
from subprocess import PIPE, CalledProcessError, Popen, run
from tempfile import TemporaryDirectory, TemporaryFile

from pydantic import BaseModel, ConfigDict, Field, RootModel
from reccy.configuration import settings, units
from tqdm import tqdm


class Movie(BaseModel, frozen=True):
    inputs: list[str] = Field(default_factory=lambda: ['clips/*.mp4'], min_length=1)
    duration: units.Seconds = 3600
    title_card: Path | None = None
    seed: int = 0
    width: units.Pixels = Field(default=1280, gt=0, le=3840, multiple_of=2)
    height: units.Pixels = Field(default=720, gt=0, le=2160, multiple_of=2)
    fps: units.WholeFramesPerSecond = Field(default=30, gt=0, le=60)
    work_scale: int = 2
    work_fps: units.WholeFramesPerSecond = 30
    still_duration: units.Seconds = 30
    start_black_duration: units.Seconds = 8
    title_interval: units.Seconds = 180
    title_jitter: units.Seconds = 30
    title_duration: units.Seconds = 8
    title_fade: units.Seconds = 4

    @property
    def resolution(self) -> str:
        return f'{self.width}x{self.height}'

    model_config = ConfigDict(extra='forbid')


def cached_movie(recipe: Movie, base: Path, renderer: Path, cache: Path) -> Path:
    inputs: list[Path] = []
    for pattern in recipe.inputs:
        matches = sorted(glob(str(base / Path(pattern).expanduser())))
        if not matches:
            raise ValueError(f'No movie inputs match {pattern}')
        inputs.extend(Path(p).resolve() for p in matches)
    title = (
        (base / recipe.title_card.expanduser()).resolve() if recipe.title_card else None
    )
    assets = [*inputs, *([title] if title is not None else [])]
    if any(not p.is_file() for p in assets) or any(
        p.suffix.lower() == '.toml' for p in inputs
    ):
        raise ValueError('Movie inputs must be existing video or image files')
    ffmpeg_version = run(['ffmpeg', '-version'], capture_output=True, check=True).stdout
    renderer_files = [
        renderer / p
        for p in (
            'scripts/render.py',
            'scripts/render_plan.py',
            'scripts/title_card.py',
            'scripts/media_output.py',
            'streamo/video_probe.py',
            'uv.lock',
        )
    ]
    sources = [*assets, *renderer_files]
    identities = [file_hash(p) for p in sources]
    digest = hashlib.sha256(recipe.model_dump_json().encode())
    digest.update(ffmpeg_version)
    for path, value in zip(sources, identities, strict=True):
        digest.update(os.fsencode(path) + b'\0')
        digest.update(value)
    output = cache / digest.hexdigest() / 'movie.mp4'
    if output.is_file():
        print('Reusing the rendered movie', flush=True)
        return output

    print('Rendering the show movie', flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / output.name
        render_and_validate(recipe, inputs, title, renderer, temporary)
        if [file_hash(p) for p in sources] != identities:
            raise ValueError('Movie inputs or renderer changed during the render')
        try:
            output.hardlink_to(temporary)
        except FileExistsError:
            # Another process published the same validated recipe first.
            if not output.is_file():
                raise
    return output


def render_and_validate(
    recipe: Movie, inputs: list[Path], title: Path | None, renderer: Path, output: Path
) -> None:
    command = ['uv', 'run', '--locked']
    arguments = [
        flag
        for key, value in recipe.model_dump(exclude={'inputs', 'title_card'}).items()
        for flag in (f'--{key.replace("_", "-")}', str(value))
    ]
    if title is not None:
        arguments.extend(['--title-card', str(title)])
    validation = output.with_name('validate.toml')
    settings.save(
        validation,
        RootModel(
            {
                'video': str(output),
                'streaming_service': {
                    'ingest': {'stream_key': 'validation-only'},
                    'encoding': {
                        'video': {
                            'resolution': recipe.resolution,
                            'frame_rate': recipe.fps,
                        }
                    },
                },
            }
        ),
    )
    try:
        render_with_progress(
            [
                *command,
                'python',
                '-m',
                'scripts.render',
                '--progress',
                '--output',
                str(output),
                *arguments,
                '--inputs',
                *(str(p) for p in inputs),
            ],
            renderer,
            recipe.duration,
        )
        print('Validating the show movie', flush=True)
        run(
            [*command, 'streamo', 'daemon', 'validate', '--config', str(validation)],
            cwd=renderer,
            capture_output=True,
            text=True,
            check=True,
        )
    except CalledProcessError as error:
        detail = error.stderr.strip() or f'exit status {error.returncode}'
        raise ValueError(detail.replace(str(output), 'rendered movie')) from error


def render_with_progress(command: list[str], renderer: Path, duration: float) -> None:
    with (
        TemporaryFile(mode='w+') as errors,
        Popen(command, cwd=renderer, stdout=PIPE, stderr=errors, text=True) as process,
        tqdm(
            total=duration,
            desc='Rendering movie',
            unit='s',
            disable=None,
            bar_format='{l_bar}{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]',
        ) as progress,
    ):
        assert process.stdout is not None
        try:
            for line in process.stdout:
                key, _, value = line.strip().partition('=')
                if key != 'rendered_seconds':
                    continue
                seconds = float(value)
                if isfinite(seconds) and seconds > progress.n:
                    progress.update(min(seconds, duration) - progress.n)
            if status := process.wait():
                errors.seek(0)
                raise CalledProcessError(status, command, stderr=errors.read())
        finally:
            if process.poll() is None:
                process.terminate()


def file_hash(path: Path) -> bytes:
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').digest()


def cache_directory() -> Path:
    return (
        Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
        / 'showco'
        / 'movies'
    )
