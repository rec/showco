from __future__ import annotations

import json
import shlex
import subprocess
import tomllib
from pathlib import Path
from unittest import mock

import pytest
from provision_helpers import make_config, values

from showco.provision import config, media, provision, script


def test_provisioning_transfers_only_streamo_publish_credentials() -> None:
    configuration = make_config(
        values(
            stream={
                'enabled': True,
                'stream_key': 'test-key',
                'client_id': 'test-client',
                'access_token': 'test-token',
                'client_secret': 'keep-local',
            }
        )
    )
    assignments = shlex.split(script.remote_command(configuration, '/tmp/provision.sh'))
    payload = next(
        a.split('=', 1)[1]
        for a in assignments
        if a.startswith('SHOWCO_STREAM_SETTINGS=')
    )
    assert json.loads(payload) == {
        'stream_key': 'test-key',
        'client_id': 'test-client',
        'access_token': 'test-token',
    }
    with mock.patch('showco.provision.media.ssh.run_ssh') as run:
        media.configure_stream_media(configuration)
    command = run.call_args.args[1]
    assert 'SHOWCO_STREAM_SETTINGS=' in command
    assert 'test-token' in command
    assert 'keep-local' not in command


def test_target_media_configuration_applies_publish_credentials(tmp_path: Path) -> None:
    target = tmp_path / 'config.toml'
    settings = {
        'stream_key': 'test-key',
        'client_id': 'test-client',
        'access_token': 'test-token',
    }
    with (
        mock.patch('sys.argv', ['media', '--config', str(target)]),
        mock.patch.dict('os.environ', {'SHOWCO_STREAM_SETTINGS': json.dumps(settings)}),
    ):
        media.main()
    value = tomllib.loads(target.read_text())['streaming_service']
    assert value['service'] == 'twitch'
    assert value['ingest']['stream_key'] == 'test-key'
    assert value['client_id'] == 'test-client'
    assert value['access_token'] == 'test-token'


def test_media_selections_persist_and_only_specified_categories_are_replaced(
    tmp_path: Path,
) -> None:
    photos = tmp_path / 'photos'
    photos.mkdir()
    other = tmp_path / 'more photos'
    other.mkdir()
    audio = tmp_path / 'intro.flac'
    audio.touch()
    movie = tmp_path / "performer's movie.mp4"
    movie.touch()
    teardown = tmp_path / 'exit'
    teardown.mkdir()
    path = tmp_path / 'config.toml'
    path.write_text('# public configuration\n')
    options = provision.DeployOptions(
        config_path=path,
        photos=[photos, other],
        audio=[audio],
        teardown=[teardown],
        movie=movie,
    )
    with (
        mock.patch(
            'showco.provision.provision.config.load_values', return_value=values()
        ),
        mock.patch('showco.provision.provision.validate_config'),
        mock.patch('showco.provision.provision.gui_schema.load_gui'),
    ):
        resolved = provision.resolved_config(options)
    media.save_sources(path, resolved.media)
    saved = config.Media.model_validate(
        tomllib.loads(path.with_name('media.toml').read_text())
    )
    assert saved.photos == [photos, other]
    assert saved.audio == [audio]
    assert saved.teardown == [teardown]
    assert saved.movie == movie
    assert path.read_text() == '# public configuration\n'
    root = Path('/home/tom/.local/share/showco/media')
    assert resolved.audio == [root / 'audio/000/intro.flac']
    assert resolved.setup == []
    assert resolved.teardown == [root / 'teardown/000']
    again = media.resolve_sources(path, provision.DeployOptions(photos=[other]))
    assert again.photos == [other]
    assert again.audio == [audio]
    assert again.teardown == [teardown]
    assert again.movie == movie
    assert media.resolve_sources(path, provision.DeployOptions()) == saved
    assignments = shlex.split(script.remote_command(resolved, '/tmp/provision.sh'))
    photos_arg = next(
        a for a in assignments if a.startswith('SHOWCO_STREAM_MEDIA_ARGS=')
    )
    assert shlex.split(photos_arg.partition('=')[2]) == [
        '--photos',
        str(root / 'photos/000'),
        '--photos',
        str(root / 'photos/001'),
        '--movie',
        str(root / 'movie/000' / movie.name),
    ]


def test_saved_relative_media_paths_are_relative_to_the_media_file(
    tmp_path: Path,
) -> None:
    (tmp_path / 'photos').mkdir()
    (tmp_path / 'media.toml').write_text('photos = ["photos"]\n')
    result = media.resolve_sources(tmp_path / 'config.toml', provision.DeployOptions())
    assert result.photos == [tmp_path / 'photos']


@pytest.mark.parametrize('kind', ['photos', 'audio', 'setup', 'teardown'])
def test_missing_local_media_is_reported_before_copying(
    tmp_path: Path, kind: str
) -> None:
    options = provision.DeployOptions.model_validate({kind: [tmp_path / 'missing']})
    with pytest.raises(SystemExit, match='does not exist'):
        media.resolve_sources(tmp_path / 'config.toml', options)


def test_scores_must_remain_usable_after_copying(tmp_path: Path) -> None:
    directory = tmp_path / 'playlist'
    directory.mkdir()
    (directory / 'one.flac').touch()
    score = directory / 'score.toml'
    score.write_text('files = ["one.flac"]\nshuffle = true\n')
    options = provision.DeployOptions(teardown=[directory])
    assert media.resolve_sources(tmp_path / 'config.toml', options).teardown == [
        directory
    ]
    score.write_text('files = ["../outside.flac"]\n')
    with pytest.raises(SystemExit, match='relative paths inside'):
        media.resolve_sources(tmp_path / 'config.toml', options)


def test_photo_settings_preserve_other_values_and_valid_weights() -> None:
    original = '''# Keep the operator's configuration
image_dir = [
    "/old/photos",
]
image_dir_weights = [3]
image_interval = 45.0
stream_title = """A multiline title
image_dir = a line of title text
[not.a.table]
"""
[streaming_service]
service = "twitch"
stream_key = "example-secret-do-not-print"
'''
    photos = [Path('/new/photos'), Path('/new/more')]
    updated = media.stream_media_configuration(original, photos)
    expected = tomllib.loads(original)
    expected['image_dir'] = [str(p) for p in photos]
    assert tomllib.loads(updated) == expected
    assert '# Keep the operator' in updated
    assert media.stream_media_configuration(updated, photos) == updated


def test_photos_enable_disabled_overlays_and_reset_incompatible_weights() -> None:
    updated = media.stream_media_configuration(
        'image_dir_weights = [4, 3, 2, 1]\nimage_interval = 0\n',
        [Path('/photos/a'), Path('/photos/b')],
    )
    values = tomllib.loads(updated)
    assert values == {
        'image_dir': ['/photos/a', '/photos/b'],
        'image_dir_weights': [2, 1],
        'image_interval': 30.0,
    }


def test_media_transfer_keeps_sources_separate_and_preserves_streamo_inbox(
    tmp_path: Path,
) -> None:
    photos = tmp_path / "performer's photos"
    photos.mkdir()
    audio = tmp_path / 'song with spaces.flac'
    audio.touch()
    configuration = make_config(values(), port=2200).model_copy(
        update={'media': config.Media(photos=[photos], teardown=[audio])}
    )
    with (
        mock.patch('showco.provision.ssh.run_ssh'),
        mock.patch('showco.provision.ssh.run_command') as copy,
        mock.patch('showco.provision.media.which', return_value='/usr/bin/rsync'),
    ):
        media.synchronize(configuration)
    photos_command, audio_command = (c.args[0] for c in copy.call_args_list)
    assert '--delete' in photos_command
    assert '--exclude=/incoming/' in photos_command
    assert '--exclude=/.streamo-*' in photos_command
    assert photos_command[-2:] == [
        str(photos) + '/',
        'tom@recs-stage.local:/home/tom/.local/share/showco/media/photos/000/',
    ]
    assert shlex.split(photos_command[photos_command.index('-e') + 1])[-2:] == [
        '-p',
        '2200',
    ]
    assert audio_command[-2:] == [
        str(audio),
        'tom@recs-stage.local:/home/tom/.local/share/showco/media/teardown/000/',
    ]


def test_failed_media_copy_stops_before_later_sources(tmp_path: Path) -> None:
    directory = tmp_path / 'photos'
    directory.mkdir()
    configuration = make_config(values()).model_copy(
        update={'media': config.Media(photos=[directory, directory])}
    )
    with (
        mock.patch('showco.provision.ssh.run_ssh'),
        mock.patch('showco.provision.media.which', return_value='/usr/bin/rsync'),
        mock.patch(
            'showco.provision.ssh.run_command',
            side_effect=subprocess.CalledProcessError(23, ['rsync']),
        ) as copy,
        pytest.raises(SystemExit, match='cannot copy photos'),
    ):
        media.synchronize(configuration)
    assert copy.call_count == 1


def test_clearing_photos_disables_only_managed_photos() -> None:
    managed = (
        'image_dir = ["/home/tom/.local/share/showco/media/photos/000"]\n'
        'image_interval = 45\n'
    )
    result = tomllib.loads(media.stream_media_configuration(managed, []))
    assert result['image_interval'] == 0
    assert result['image_dir'] == tomllib.loads(managed)['image_dir']
    custom = 'image_dir = ["/custom/photos"]\nimage_interval = 45\n'
    assert media.stream_media_configuration(custom, []) == custom
    with pytest.raises(ValueError, match='remove image_feed'):
        media.stream_media_configuration(
            managed + '[image_feed]\nurl = "example"\n', []
        )


@pytest.mark.parametrize('interval', ['0 seconds', '0 ms'])
def test_photos_enable_intervals_written_with_units(interval: str) -> None:
    updated = media.stream_media_configuration(
        f'image_interval = "{interval}"\n', [Path('/photos')]
    )
    assert tomllib.loads(updated)['image_interval'] == 30.0


@pytest.mark.parametrize('kind', ['photos', 'audio', 'setup', 'teardown', 'movie'])
def test_every_media_category_compares_contents_before_copying(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / 'source'
    if kind == 'photos':
        source.mkdir()
    else:
        source.touch()
    selection = source if kind == 'movie' else [source]
    configuration = make_config(values()).model_copy(
        update={'media': config.Media.model_validate({kind: selection})}
    )
    with (
        mock.patch('showco.provision.ssh.run_ssh'),
        mock.patch('showco.provision.ssh.run_command') as copy,
        mock.patch('showco.provision.media.which', return_value='/usr/bin/rsync'),
    ):
        media.synchronize(configuration)
    assert '--checksum' in copy.call_args.args[0]


def test_movie_configuration_replaces_only_the_video_source() -> None:
    original = (
        'video = "/old/movie.mp4"\n'
        'image_dir = ["/custom/photos"]\nimage_interval = 45\n'
        '[streaming_service]\nservice = "twitch"\n'
    )
    movie = Path("/home/tom/.local/share/showco/media/movie/000/performer's movie.mp4")
    updated = media.stream_media_configuration(original, [], movie)
    expected = tomllib.loads(original)
    expected['video'] = str(movie)
    assert tomllib.loads(updated) == expected
    assert media.stream_media_configuration(updated, [], movie) == updated


@pytest.mark.parametrize('exists', [False, True])
def test_movie_must_be_an_existing_file(tmp_path: Path, exists: bool) -> None:
    source = tmp_path / 'movie'
    if exists:
        source.mkdir()
    with pytest.raises(SystemExit, match='movie'):
        media.resolve_sources(
            tmp_path / 'config.toml', provision.DeployOptions(movie=source)
        )
