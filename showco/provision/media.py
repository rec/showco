"""Local media selections, transfer to the Pi, and streamO media configuration."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import time
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, CalledProcessError, TimeoutExpired, run
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
from . import config, ssh, state
from .media_package import FileMetadata, MediaInventory, stage_media

if TYPE_CHECKING:
    from .provision import DeployOptions


class StreamMediaOptions(BaseModel, frozen=True):
    config_path: Annotated[Path, tyro.conf.arg(name='config')]
    movie: Path | None = None
    movie_resolution: str | None = None
    movie_frame_rate: int | None = None
    photos: Annotated[list[Path], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )
    recs_device: Annotated[list[str], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )


def resolve_sources(
    config_path: Path,
    options: DeployOptions,
    *,
    defaults: config.Media | None = None,
    use_saved_movie: bool = True,
) -> config.Media:
    path = config_path.expanduser().resolve().with_name('media.toml')
    try:
        saved = config.Media.model_validate(
            config.merge_values(
                (defaults or config.Media()).model_dump(), config.read_toml(path)
            )
        )
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
            if use_saved_movie and saved.movie is not None
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


def target_paths(provision_config: config.Config, kind: str) -> list[Path]:
    package = provision_config.media_package
    root = (
        Path('/home')
        / provision_config.network.user
        / '.local/share/showco/media/versions'
        / package.version
    )
    return [root / p for p in package.targets[kind]]


def synchronize(provision_config: config.Config) -> None:
    sources = provision_config.media
    if not any(sources.sources.values()):
        return
    package = provision_config.media_package
    root = Path('/home') / provision_config.network.user / '.local/share/showco/media'
    versions = root / 'versions'
    destination = versions / package.version
    quoted_destination = shlex.quote(str(destination))
    verify = media_verification_command(package)
    verify_destination = prepare_package_command(
        destination, root, package, verify, len(sources.photos)
    )
    if (
        ssh.capture_ssh(
            provision_config,
            f'if test -e {quoted_destination} || test -L {quoted_destination}; '
            "then printf 'present\\n'; fi",
        )
        == 'present'
    ):
        print('Verifying existing media package', flush=True)
        ssh.run_ssh(provision_config, verify_destination, timeout_seconds=300)
        print('Reusing verified media package', flush=True)
        return

    if not (rsync := which('rsync')):
        sys.exit('ERROR: rsync is required on the development machine to copy media')
    check_media_space(provision_config, versions, package)
    transport = shlex.join(
        ssh.ssh_command(provision_config, provision_config.ssh_target, '')[:-2]
    )
    staging = versions / f'.staging-{uuid4().hex}'
    quoted_staging = shlex.quote(str(staging))
    stage_attempted = False
    try:
        ssh.run_ssh(
            provision_config,
            'command -v rsync >/dev/null || '
            '{ sudo apt-get update && sudo apt-get install -y rsync; }',
            timeout_seconds=300,
        )
        stage_attempted = True
        ssh.run_ssh(provision_config, f'mkdir -- {quoted_staging}', timeout_seconds=300)
        reused: frozenset[str] = frozenset()
        if package.metadata:
            names = list(package.metadata)
            copied = ssh.capture_ssh(
                provision_config,
                reuse_media_command(versions, staging, package),
                timeout_seconds=300,
            )
            reused = frozenset(names[int(i)] for i in copied.splitlines())
            if reused:
                print(
                    f'Reusing {len(reused)} media files already on the Pi', flush=True
                )
        with stage_media(package, reused) as local:
            interactive = sys.stdout.isatty()
            progress_options: list[str] = []
            if interactive:
                supports_total = (
                    run(
                        [rsync, '--info=help'],
                        stdout=DEVNULL,
                        stderr=DEVNULL,
                        check=False,
                    ).returncode
                    == 0
                )
                progress_options = (
                    ['--info=progress2', '--no-inc-recursive']
                    if supports_total
                    else ['--progress']
                )
                print('Uploading media package', flush=True)
            ssh.run_command(
                [
                    'rsync',
                    '-rt',
                    *(['--checksum'] if provision_config.verify_media else []),
                    '--timeout=60',
                    *progress_options,
                    '-e',
                    transport,
                    str(local) + '/',
                    f'{provision_config.ssh_target}:{quoted_staging}/',
                ],
                stream_output=interactive,
            )
            # GNU mv -T -n never replaces an existing version, including when a
            # concurrent deploy publishes the same version first. Verify it too.
            staging_check = prepare_package_command(
                staging, root, package, verify, len(sources.photos)
            )
            if not provision_config.verify_media and (
                timestamps := media_timestamp_command(staging, package, skip=reused)
            ):
                # Older rsync peers can truncate modification times to whole seconds.
                staging_check = f'{timestamps} && {staging_check}'
            command = (
                f'{staging_check} && '
                f'mv -T -n -- {quoted_staging} '
                f'{quoted_destination} && {verify_destination}'
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
    finally:
        if stage_attempted:
            try:
                ssh.run_ssh(
                    provision_config,
                    f'rm -rf --one-file-system -- {quoted_staging}',
                    exit_on_error=False,
                    timeout_seconds=15,
                )
            except (OSError, CalledProcessError, TimeoutExpired) as error:
                print(
                    f'WARNING: Could not remove staging directory {staging}: {error}',
                    file=sys.stderr,
                )


def check_media_space(
    provision_config: config.Config, versions: Path, package: MediaInventory
) -> None:
    target = shlex.quote(str(versions))
    result = ssh.capture_ssh(
        provision_config, f'mkdir -p -- {target} && df -B1 --output=avail -- {target}'
    )
    try:
        available = int(result.splitlines()[-1])
    except (IndexError, ValueError):
        sys.exit(f'ERROR: cannot determine free space for media packages at {versions}')
    required = sum(m.size for m in package.metadata.values())
    if package.manifest is not None:
        required += len(package.manifest_bytes) + len(package.checksum_bytes)
    if available < required:
        sys.exit(
            f'ERROR: not enough free space to stage replacement media at {versions}: '
            f'{required:,} bytes required, {available:,} available. '
            'Existing packages were kept.'
        )


def reuse_media_command(versions: Path, staging: Path, package: MediaInventory) -> str:
    command = [
        f'versions={shlex.quote(str(versions))}',
        f'staging={shlex.quote(str(staging))}',
        """reuse_media() {
    index=$1 name=$2 size=$3 metadata=$4 digest=$5
    for version in "$versions"/*; do
        id=${version##*/}
        case "$id" in *[!0-9a-f]*) continue ;; esac
        [ "${#id}" -eq 64 ] && [ -d "$version" ] && [ ! -L "$version" ] || continue
        source=$version/$name
        [ -f "$source" ] && [ ! -L "$source" ] || continue
        if [ -n "$digest" ]; then
            [ "$(stat -c '%s' -- "$source")" = "$size" ] || continue
            checksum=$(sha256sum < "$source") || return 1
            [ "${checksum%% *}" = "$digest" ] || continue
        else
            [ "$(TZ=UTC0 stat -c '%s %y' -- "$source")" = "$metadata" ] || continue
        fi
        target=$staging/$name
        mkdir -p -- "${target%/*}" || return 1
        cp -p --reflink=auto -- "$source" "$target" || return 1
        printf '%s\\n' "$index"
        return
    done
    return 0
}""",
    ]
    for index, (name, metadata) in enumerate(package.metadata.items()):
        digest = '' if package.manifest is None else package.manifest.files[name].sha256
        command.append(
            shlex.join(
                [
                    'reuse_media',
                    str(index),
                    name,
                    str(metadata.size),
                    media_stat(metadata),
                    digest,
                ]
            )
            + ' || exit 1'
        )
    return '\n'.join(command)


def media_stat(metadata: FileMetadata) -> str:
    seconds, nanos = divmod(metadata.modified_ns, 1_000_000_000)
    stamp = datetime.fromtimestamp(seconds, UTC).strftime('%Y-%m-%d %H:%M:%S')
    return f'{metadata.size} {stamp}.{nanos:09d} +0000'


def prepare_package_command(
    destination: Path, root: Path, package: MediaInventory, verify: str, photos: int
) -> str:
    command = f'cd {shlex.quote(str(destination))} && {verify}'
    for index in range(photos):
        inbox = root / 'photos' / f'{index:03d}' / 'incoming'
        link = destination / 'photos' / f'{index:03d}' / 'incoming'
        command += (
            f' && mkdir -p -- {shlex.quote(str(inbox))}'
            f' && ln -sfn -- {shlex.quote(str(inbox))} {shlex.quote(str(link))}'
        )
    if timestamps := media_timestamp_command(destination, package):
        command += f' && {timestamps}'
    return command


def media_timestamp_command(
    destination: Path, package: MediaInventory, *, skip: frozenset[str] = frozenset()
) -> str:
    commands = []
    for name, metadata in package.metadata.items():
        if name in skip:
            continue
        path = shlex.quote(f'./{name}')
        modified = Decimal(metadata.modified_ns).scaleb(-9)
        accessed = Decimal(metadata.accessed_ns).scaleb(-9)
        commands.extend(
            [
                f'test -f {path} && test ! -L {path}',
                f'touch -c -h -m -d @{modified:f} -- {path}',
                f'touch -c -h -a -d @{accessed:f} -- {path}',
            ]
        )
    if not commands:
        return ''
    return ' && '.join([f'cd {shlex.quote(str(destination))}', *commands])


def media_verification_command(package: MediaInventory) -> str:
    if package.manifest is not None:
        checksums = hashlib.sha256(package.checksum_bytes).hexdigest()
        return (
            f"printf '%s  SHA256SUMS\\n' {checksums} | "
            'sha256sum --strict -c - && sha256sum --strict -c SHA256SUMS'
        )
    checks = [
        f'test -d {shlex.quote(f"./{d}")} && test ! -L {shlex.quote(f"./{d}")}'
        for d in package.directories
    ]
    checks.append(
        """test "$(find . -type f ! -path ./SHA256SUMS """
        """! -path ./asset-manifest.toml -printf .)" = """
        + shlex.quote('.' * len(package.metadata))
    )
    for name, metadata in package.metadata.items():
        path = shlex.quote(f'./{name}')
        expected = shlex.quote(media_stat(metadata))
        checks.append(
            f'test -f {path} && test ! -L {path} && '
            f"""test "$(TZ=UTC0 stat -c '%s %y' -- {path})" = {expected}"""
        )
    return (
        '{ ' + ' && '.join(checks) + ' || '
        "{ printf '%s\\n' 'Media package names, sizes or modification times differ' "
        '>&2; exit 1; }; }'
    )


def configure_stream_media(
    provision_config: config.Config, *, expected_fingerprint: str
) -> None:
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
    fingerprint = shlex.quote(
        str(state.provisioning_fingerprint_path(provision_config))
    )
    apply = (
        f'test "$(cat -- {fingerprint})" = {shlex.quote(expected_fingerprint)} || {{ '
        "printf '%s\\n' 'ERROR: provisioning state changed while preparing media; "
        "rerun showCo.' >&2; exit 1; }; "
        f'cd {shlex.quote(str(provision_config.paths.root / "showco"))} && '
        f'export PATH="$HOME/.local/bin:$PATH" && {command}'
    )
    ssh.run_ssh(
        provision_config,
        state.lock_configuration(provision_config, apply),
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
        for p in target_paths(provision_config, 'photos')
        for v in ['--photos', str(p)]
    ]
    if movies := target_paths(provision_config, 'movie'):
        arguments.extend(['--movie', str(movies[0])])
        if recipe := provision_config.movie:
            arguments.extend(
                [
                    '--movie-resolution',
                    recipe.resolution,
                    '--movie-frame-rate',
                    str(recipe.fps),
                ]
            )
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
        if (
            options.movie_resolution is not None
            and options.movie_frame_rate is not None
        ):
            values = tomllib.loads(updated)
            configured = config.merge_values(
                values,
                {
                    'allow_scaling': False,
                    'streaming_service': {
                        'encoding': {
                            'video': {
                                'resolution': options.movie_resolution,
                                'frame_rate': options.movie_frame_rate,
                            }
                        }
                    },
                },
            )
            if updated != text or configured != values:
                settings.save(path, RootModel(configured), write_empty_dicts=True)
        elif updated != text:
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
    except (OSError, ValueError, ReccyError) as error:
        sys.exit(f'ERROR: cannot configure streamO in {path}: {error}')


if __name__ == '__main__':
    main()
