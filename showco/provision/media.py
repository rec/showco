"""Local media selections, transfer to the Pi, and streamO media configuration."""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
import tomllib
from pathlib import Path
from shutil import which
from subprocess import CalledProcessError, TimeoutExpired
from typing import TYPE_CHECKING, Annotated

import tyro
from pydantic import BaseModel, Field, TypeAdapter
from reccy.configuration import units
from reccy.runtime.files import atomic_output

from ..runtime.recs_control import RecsControlClient
from ..streamo.config import configure_stream_settings
from ..x18.music import Files
from . import config, ssh

if TYPE_CHECKING:
    from .provision import DeployOptions


class StreamMediaOptions(BaseModel, frozen=True):
    config_path: Annotated[Path, tyro.conf.arg(name='config')]
    movie: Path | None = None
    photos: Annotated[list[Path], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )
    recs_device: Annotated[list[str], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )


def resolve_sources(config_path: Path, options: DeployOptions) -> config.Media:
    path = config_path.expanduser().resolve().with_name('media.toml')
    try:
        saved = config.Media.model_validate(config.read_toml(path))
        selected: dict[str, object] = {
            n: [p.expanduser().resolve() for p in getattr(options, n)]
            if getattr(options, n)
            else [(path.parent / p.expanduser()).resolve() for p in ps]
            for n, ps in saved.sources.items()
            if n != 'movie'
        }
        selected['movie'] = (
            options.movie.expanduser().resolve()
            if options.movie is not None
            else (path.parent / saved.movie.expanduser()).resolve()
            if saved.movie is not None
            else None
        )
        sources = config.Media.model_validate(selected)
        for name, paths in sources.sources.items():
            for source in paths:
                if name == 'photos' and not source.is_dir():
                    sys.exit(f'ERROR: photo directory does not exist: {source}')
                if not source.is_file() and not source.is_dir():
                    sys.exit(f'ERROR: {name} source does not exist: {source}')
                if name == 'movie' and not source.is_file():
                    sys.exit(f'ERROR: movie must be a file: {source}')
                score = source / 'score.toml'
                if name != 'photos' and source.is_dir() and score.is_file():
                    playlist = Files.model_validate(tomllib.loads(score.read_text()))
                    for entry in playlist.files:
                        if entry.is_absolute() or not (
                            source / entry
                        ).resolve().is_relative_to(source):
                            sys.exit(
                                f'ERROR: {score}: files must be relative paths '
                                f'inside {source}'
                            )
                        if not (source / entry).is_file():
                            sys.exit(
                                f'ERROR: {score}: audio file does not exist: {entry}'
                            )
        return sources
    except (OSError, ValueError) as error:
        sys.exit(f'ERROR: invalid media selections in {path}: {error}')


def save_sources(config_path: Path, sources: config.Media) -> None:
    path = config_path.expanduser().resolve().with_name('media.toml')
    if not path.exists() and not any(sources.sources.values()):
        return
    text = ''.join(
        f'{n} = {json.dumps(v, ensure_ascii=False)}\n'
        for n, v in sources.model_dump(mode='json').items()
        if v is not None
    )
    try:
        with atomic_output(path) as temporary:
            temporary.write_text(text)
    except OSError as error:
        sys.exit(f'ERROR: cannot save media selections to {path}: {error}')


def target_paths(sources: config.Media, user: str, kind: str) -> list[Path]:
    root = Path('/home') / user / '.local/share/showco/media' / kind
    return [
        root / f'{i:03d}' / p.name if p.is_file() else root / f'{i:03d}'
        for i, p in enumerate(sources.sources[kind])
    ]


def synchronize(provision_config: config.Config) -> None:
    sources = provision_config.media
    if not any(sources.sources.values()):
        return
    if not which('rsync'):
        sys.exit('ERROR: rsync is required on the development machine to copy media')
    # rsync is also in the normal base-package list, but a new card needs it
    # before the provisioning script installs and starts the media consumers.
    ssh.run_ssh(
        provision_config,
        'command -v rsync >/dev/null || '
        '{ sudo apt-get update && sudo apt-get install -y rsync; }',
        timeout_seconds=300,
    )
    transport = shlex.join(
        ssh.ssh_command(provision_config, provision_config.ssh_target, '')[:-2]
    )
    for kind, paths in sources.sources.items():
        targets = target_paths(sources, provision_config.network.user, kind)
        for source, target in zip(paths, targets, strict=True):
            directory = target.parent if source.is_file() else target
            ssh.run_ssh(provision_config, f'mkdir -p -- {shlex.quote(str(directory))}')
            command = [
                'rsync',
                '-rtL',
                '--checksum',
                '--timeout=60',
                '-e',
                transport,
            ]
            if source.is_dir():
                command.append('--delete')
            if kind == 'photos':
                command.extend(['--exclude=/incoming/', '--exclude=/.streamo-*'])
            command.extend(
                [
                    str(source) + ('/' if source.is_dir() else ''),
                    f'{provision_config.ssh_target}:{shlex.quote(str(directory))}/',
                ]
            )
            print(f'Synchronizing {kind}: {source}...')
            try:
                ssh.run_command(command)
            except (OSError, CalledProcessError, TimeoutExpired) as error:
                sys.exit(f'ERROR: cannot copy {kind} source {source}: {error}')


def configure_stream_media(provision_config: config.Config) -> None:
    arguments = streamo_arguments(provision_config)
    path = Path('/home') / provision_config.network.user / '.config/streamo/config.toml'
    command = shlex.join(
        [
            'uv',
            'run',
            '--locked',
            'python',
            '-m',
            'showco.provision.media',
            '--config',
            str(path),
            *arguments,
        ]
    )
    settings = streamo_settings(provision_config)
    command = f'SHOWCO_STREAM_SETTINGS={shlex.quote(settings)} {command}'
    if not arguments and not settings:
        command = (
            'if grep -Fq '
            f'-e {shlex.quote("/.local/share/showco/media/photos/")} '
            f'-e {shlex.quote("/.local/share/showco/media/movie/")} '
            f'{shlex.quote(str(path))} 2>/dev/null; then {command}; fi'
        )
    ssh.run_ssh(
        provision_config,
        f'cd {shlex.quote(str(provision_config.paths.root / "showco"))} && '
        f'export PATH="$HOME/.local/bin:$PATH" && {command}',
        timeout_seconds=60,
    )


def streamo_settings(provision_config: config.Config) -> str:
    settings = {
        k: v
        for k, v in provision_config.stream.model_dump(
            include={'stream_key', 'client_id', 'access_token'}
        ).items()
        if v
    }
    return json.dumps(settings) if provision_config.stream.enabled and settings else ''


def streamo_arguments(provision_config: config.Config) -> list[str]:
    arguments = [
        v
        for p in target_paths(
            provision_config.media, provision_config.network.user, 'photos'
        )
        for v in ['--photos', str(p)]
    ]
    if movies := target_paths(
        provision_config.media, provision_config.network.user, 'movie'
    ):
        arguments.extend(['--movie', str(movies[0])])
    if provision_config.stream.enabled:
        for mixer in provision_config.mixers:
            if mixer.name == 'X18':
                for name in mixer.audio_device_names:
                    arguments.extend(['--recs-device', name])
    return arguments


def forwarded_audio_source(selectors: list[str]) -> str:
    deadline = time.monotonic() + 30
    error = 'no matching device in the recs catalogue'
    while True:
        try:
            reply = RecsControlClient().call('list_devices', timeout=1)
            devices = reply.get('devices', []) if isinstance(reply, dict) else []
            names = [
                d['name']
                for d in devices
                if isinstance(d, dict)
                and isinstance(d.get('name'), str)
                and any(d['name'].startswith(s) for s in selectors)
            ]
            if len(names) == 1:
                return names[0]
            if len(names) > 1:
                raise ValueError(f'Ambiguous recs stream input: {names}')
        except (ConnectionError, OSError, TimeoutError) as failure:
            error = str(failure)
        if time.monotonic() >= deadline:
            raise ValueError(f'Cannot select recs input for {selectors}: {error}')
        time.sleep(1)


def stream_media_configuration(
    text: str,
    photos: list[Path],
    movie: Path | None = None,
    *,
    recs_source: str | None = None,
) -> str:
    """Replace root media settings without reserializing credentials or tables."""
    values = tomllib.loads(text)
    previous_movie = values.get('video')
    remove_movie = (
        movie is None
        and isinstance(previous_movie, str)
        and '/.local/share/showco/media/movie/' in previous_movie
    )
    if remove_movie:
        encoding = values.get('streaming_service', {}).get('encoding', {})
        if encoding.get('video') is not None:
            raise ValueError(
                'A replacement movie is required for video streaming; '
                'configuration was not changed'
            )
    replacements: dict[str, object] = {'video': str(movie)} if movie is not None else {}
    if photos:
        replacements['image_dir'] = [str(p) for p in photos]
        if (
            TypeAdapter(units.Seconds).validate_python(values.get('image_interval', 0))
            == 0
        ):
            replacements['image_interval'] = 30.0
        weights = values.get('image_dir_weights')
        if weights is not None:
            if isinstance(weights, str):
                count = len(weights.split(','))
            elif isinstance(weights, list):
                count = len(weights)
            else:
                raise ValueError('image_dir_weights must be a string or integer list')
            if count > len(photos):
                replacements['image_dir_weights'] = list(range(len(photos), 0, -1))
    elif any(
        '/.local/share/showco/media/photos/' in p for p in values.get('image_dir', [])
    ):
        if values.get('image_feed'):
            raise ValueError('remove image_feed before disabling managed photos')
        replacements['image_interval'] = 0.0
    removed = (
        {'device_name', 'channel', 'sample_rate', 'recs_input'}
        if recs_source
        else set()
    )
    if remove_movie:
        removed.add('video')
    if not replacements and not recs_source and not removed:
        return text
    lines = text.splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith('['):
            in_recs_input = False
            for line in lines[index:]:
                if line.lstrip().startswith('['):
                    in_recs_input = line.strip() == '[recs_input]'
                if not recs_source or not in_recs_input:
                    kept.append(line)
            break
        assignment = line
        index += 1
        while True:
            try:
                parsed = tomllib.loads(assignment)
                break
            except tomllib.TOMLDecodeError:
                assignment += lines[index]
                index += 1
        if not any(k in replacements or k in removed for k in parsed):
            kept.append(assignment)
    audio = ''
    if recs_source:
        existing = values.get('recs_input', {})
        channel = values.get('channel', 17)
        channels = existing.get('channels', [channel, channel + 1])
        audio = (
            'recs_input = { daemon = true, source = '
            + json.dumps(recs_source)
            + ', channels = '
            + json.dumps(channels)
            + ' }\n'
        )
    return (
        audio
        + ''.join(f'{k} = {json.dumps(v)}\n' for k, v in replacements.items())
        + ''.join(kept)
    )


def main() -> None:
    options = tyro.cli(StreamMediaOptions)
    path = options.config_path.expanduser()
    try:
        text = path.read_text() if path.exists() else ''
        source = (
            forwarded_audio_source(options.recs_device) if options.recs_device else None
        )
        updated = stream_media_configuration(
            text, options.photos, options.movie, recs_source=source
        )
        if updated != text:
            with atomic_output(path) as temporary:
                temporary.write_text(updated)
        if settings := os.environ.get('SHOWCO_STREAM_SETTINGS'):
            stream = config.Stream.model_validate_json(settings)
            configure_stream_settings(
                path,
                {
                    k: v
                    for k, v in stream.model_dump(
                        include={'stream_key', 'client_id', 'access_token'}
                    ).items()
                    if v
                },
            )
    except (OSError, ValueError) as error:
        sys.exit(f'ERROR: cannot configure streamO in {path}: {error}')


if __name__ == '__main__':
    main()
