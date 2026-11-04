"""Local media selections, transfer to the Pi, and streamO media configuration."""

from __future__ import annotations

import hashlib
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
from uuid import uuid4

import tyro
from pydantic import BaseModel, Field, RootModel, TypeAdapter
from reccy.configuration import settings, units
from reccy.errors import ReccyError
from reccy.runtime.assets import AssetCacheError

from ..runtime.recs_control import RecsControlClient
from ..streamo.config import configure_stream_settings
from ..x18.music import Files
from . import config, ssh
from .media_package import stage_media

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
                    playlist = Files.model_validate(settings.read(score))
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
    try:
        settings.save(
            path, RootModel(sources.model_dump(mode='json', exclude_none=True))
        )
    except ReccyError as error:
        sys.exit(f'ERROR: cannot save media selections to {path}: {error}')


def target_paths(sources: config.Media, user: str, kind: str) -> list[Path]:
    root = (
        Path('/home')
        / user
        / '.local/share/showco/media/versions'
        / sources.package.version
    )
    return [root / p for p in sources.package.targets[kind]]


def synchronize(provision_config: config.Config) -> None:
    sources = provision_config.media
    if not any(sources.sources.values()):
        return
    if not which('rsync'):
        sys.exit('ERROR: rsync is required on the development machine to copy media')
    transport = shlex.join(
        ssh.ssh_command(provision_config, provision_config.ssh_target, '')[:-2]
    )
    root = Path('/home') / provision_config.network.user / '.local/share/showco/media'
    destination = root / 'versions' / sources.package.version
    staging = root / 'versions' / f'.staging-{uuid4().hex}'
    try:
        with stage_media(sources.package) as local:
            checksums = hashlib.sha256((local / 'SHA256SUMS').read_bytes()).hexdigest()
            ssh.run_ssh(
                provision_config,
                'command -v rsync >/dev/null || '
                '{ sudo apt-get update && sudo apt-get install -y rsync; }',
                timeout_seconds=300,
            )
            ssh.run_ssh(provision_config, f'mkdir -p -- {shlex.quote(str(staging))}')
            ssh.run_command(
                [
                    'rsync',
                    '-rt',
                    '--checksum',
                    '--timeout=60',
                    '-e',
                    transport,
                    str(local) + '/',
                    f'{provision_config.ssh_target}:{shlex.quote(str(staging))}/',
                ]
            )
            verify = (
                f"printf '%s  SHA256SUMS\\n' {shlex.quote(checksums)} | "
                'sha256sum --strict -c - && '
                'sha256sum --strict -c SHA256SUMS'
            )
            # GNU mv -T -n never replaces an existing version, including when a
            # concurrent deploy publishes the same version first. Verify it too.
            command = (
                f'cd {shlex.quote(str(staging))} && {verify} && '
                f'mv -T -n -- {shlex.quote(str(staging))} '
                f'{shlex.quote(str(destination))} && '
                f'cd {shlex.quote(str(destination))} && {verify}'
            )
            for index, _ in enumerate(sources.photos):
                inbox = root / 'photos' / f'{index:03d}' / 'incoming'
                link = destination / 'photos' / f'{index:03d}' / 'incoming'
                command += (
                    f' && mkdir -p -- {shlex.quote(str(inbox))}'
                    f' && ln -sfn -- {shlex.quote(str(inbox))} {shlex.quote(str(link))}'
                )
            ssh.run_ssh(provision_config, command, timeout_seconds=300)
    except (
        OSError,
        ValueError,
        AssetCacheError,
        CalledProcessError,
        TimeoutExpired,
    ) as error:
        sys.exit(f'ERROR: cannot copy media package: {error}')


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
            f'-e {shlex.quote("/.local/share/showco/media/versions/")} '
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
        and (
            '/.local/share/showco/media/movie/' in previous_movie
            or '/.local/share/showco/media/versions/' in previous_movie
        )
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
        '/.local/share/showco/media/photos/' in p
        or '/.local/share/showco/media/versions/' in p
        for p in values.get('image_dir', [])
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
            settings.write_text_atomically(path, updated)
        if stream_settings := os.environ.get('SHOWCO_STREAM_SETTINGS'):
            stream = config.Stream.model_validate_json(stream_settings)
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
