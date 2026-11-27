import subprocess
import sys
from pathlib import Path
from shutil import which
from unittest import mock

import pytest
from PIL import Image
from provision_helpers import networks, values
from reccy.configuration.settings import write_text_atomically

from showco import cli
from showco.provision import config, media, movie, provision


def test_render_command_prints_the_deploy_cache_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / 'config.toml'
    config_path.write_text('[movie]\ninputs = ["clips/*.mp4"]\n')
    secrets_path = tmp_path / 'secrets.toml'
    secrets_path.write_text('[movie]\nduration = "15min"\n')
    cache = tmp_path / 'cache' / 'showco' / 'movies'
    output = cache / 'digest' / 'movie.mp4'
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(cli, 'ERROR_LOG_PATH', tmp_path / 'showco-errors.txt')

    with mock.patch.object(movie, 'cached_movie', return_value=output) as generate:
        assert (
            cli.main(
                ['render', '--config', str(config_path), '--secrets', str(secrets_path)]
            )
            == 0
        )

    generate.assert_called_once_with(
        movie.Movie(inputs=['clips/*.mp4'], duration=900),
        tmp_path,
        Path(__file__).resolve().parents[2] / 'streamo',
        cache,
        offline=False,
    )
    assert capsys.readouterr().out.strip() == str(output)


def test_provisioning_uses_generated_movie_and_static_photos(tmp_path: Path) -> None:
    photos = tmp_path / 'rouen-images'
    photos.mkdir()
    (photos / 'existing.jpg').write_bytes(b'photo')
    generated = tmp_path / 'generated.mp4'
    generated.write_bytes(b'movie')
    write_text_atomically(tmp_path / 'media.toml', 'movie = "missing-old-movie.mp4"\n')
    settings = values(
        movie={'inputs': ['clips/*.mp4'], 'duration': '4h'},
        media={'photos': ['rouen-images']},
        networks=networks(
            internal_wifi={'password': 'test-password'},
            external_wifi={'name': 'house', 'password': 'test-password'},
        ),
    )
    options = provision.DeployOptions(config_path=tmp_path / 'config.toml')
    with (
        mock.patch.object(config, 'load_values', return_value=settings),
        mock.patch.object(movie, 'cached_movie', return_value=generated) as generate,
    ):
        resolved = provision.resolved_config(options)
    assert resolved.movie is not None
    assert generate.call_args.args[:2] == (resolved.movie, tmp_path)
    assert resolved.media.photos == [photos]
    assert resolved.media.movie == generated
    assert media.streamo_arguments(resolved)[-4:] == [
        '--movie-resolution',
        '640x360',
        '--movie-frame-rate',
        '24',
    ]


def test_no_media_does_not_read_saved_selections_or_render(tmp_path: Path) -> None:
    options = provision.DeployOptions(
        config_path=tmp_path / 'config.toml', no_media=True
    )
    public = values(
        movie={'inputs': ['missing/*.mp4']},
        networks=networks(
            internal_wifi={'password': 'test-password'},
            external_wifi={'name': 'house', 'password': 'test-password'},
        ),
    )
    with (
        mock.patch.object(config, 'load_values', return_value=public),
        mock.patch.object(media, 'resolve_sources') as resolve,
        mock.patch.object(movie, 'cached_movie') as render,
    ):
        provision.resolved_config(options)
    resolve.assert_not_called()
    render.assert_not_called()


def test_movie_cache_reuses_validated_output_and_hashes_source_contents(
    tmp_path: Path,
) -> None:
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'old')
    renderer = tmp_path / 'streamo'
    for name in (
        'scripts/render.py',
        'scripts/render_plan.py',
        'scripts/title_card.py',
        'scripts/media_output.py',
        'streamo/video_probe.py',
        'uv.lock',
    ):
        path = renderer / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    recipe = movie.Movie(inputs=['*.mp4'])
    cache = tmp_path / 'cache'

    def render(
        recipe: movie.Movie,
        inputs: list[Path],
        title: Path | None,
        renderer: Path,
        output: Path,
        *,
        offline: bool,
    ) -> None:
        output.write_bytes(inputs[0].read_bytes())

    with (
        mock.patch.object(
            movie, 'run', return_value=subprocess.CompletedProcess([], 0, b'ffmpeg')
        ),
        mock.patch.object(movie, 'render_and_validate', side_effect=render) as build,
    ):
        first = movie.cached_movie(recipe, tmp_path, renderer, cache, offline=True)
        assert build.call_args.kwargs['offline'] is True
        assert movie.cached_movie(recipe, tmp_path, renderer, cache) == first
        source.write_bytes(b'new')
        second = movie.cached_movie(recipe, tmp_path, renderer, cache)
    assert build.call_count == 2
    assert first.read_bytes() == b'old'
    assert second.read_bytes() == b'new'


def test_sibling_renderer_produces_a_validated_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    renderer = Path(__file__).resolve().parents[2] / 'streamo'
    if not (renderer / 'scripts/render.py').is_file() or not all(
        which(p) for p in ('uv', 'ffmpeg', 'ffprobe')
    ):
        pytest.skip('Requires the sibling streamO checkout, uv, and FFmpeg')
    monkeypatch.setenv('UV_OFFLINE', '1')
    Image.new('RGB', (64, 48), 'red').save(tmp_path / 'still.png')
    recipe = movie.Movie(
        inputs=['still.png'],
        duration=3,
        width=64,
        height=48,
        fps=30,
        work_scale=1,
        start_black_duration=1,
        title_fade=0,
    )
    cache = tmp_path / 'cache'
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: True)
    output = movie.cached_movie(recipe, tmp_path, renderer, cache)
    captured = capsys.readouterr()
    assert 'Rendering movie: 100%' in captured.err
    assert 'Validating the show movie' in captured.out
    with mock.patch.object(movie, 'render_and_validate') as build:
        assert movie.cached_movie(recipe, tmp_path, renderer, cache) == output
    build.assert_not_called()
