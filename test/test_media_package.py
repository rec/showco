import os
from pathlib import Path
from unittest import mock

import pytest
from reccy.runtime.asset_sets import verify_package

from showco.provision.media_package import inventory, stage_media


def test_version_depends_on_bytes_and_preserves_inbox(tmp_path: Path) -> None:
    photos = tmp_path / 'photos'
    photos.mkdir()
    (photos / 'one.jpg').write_bytes(b'image')
    (photos / 'incoming').mkdir()
    (photos / 'incoming/upload.jpg').write_bytes(b'live upload')
    selected = inventory({'photos': [photos]}, verify_media=True)
    assert (
        selected.version == inventory({'photos': [photos]}, verify_media=True).version
    )
    assert selected.manifest is not None
    with stage_media(selected) as root:
        verify_package(root, selected.manifest)
        assert (root / 'photos/000/one.jpg').read_bytes() == b'image'
        assert not (root / 'photos/000/incoming').exists()
    (photos / 'one.jpg').write_bytes(b'changed')
    assert (
        selected.version != inventory({'photos': [photos]}, verify_media=True).version
    )
    with pytest.raises(ValueError, match='changed after preflight'):
        with stage_media(selected):
            pytest.fail('Changed media must not reach transfer')


def test_inventory_reports_all_missing_files(tmp_path: Path) -> None:
    from reccy.runtime.asset_sets import AssetPreparationError

    with pytest.raises(AssetPreparationError) as error:
        inventory(
            {'photos': [tmp_path / 'first', tmp_path / 'second']}, verify_media=False
        )
    assert len(error.value.failures) == 2


def test_inventory_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / 'photos'
    source.mkdir()
    (source / 'outside').symlink_to(tmp_path / 'secret')
    with pytest.raises(ValueError, match='symlinks'):
        inventory({'photos': [source]}, verify_media=False)


def test_metadata_mode_uses_size_and_mtime_without_hashing(tmp_path: Path) -> None:
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'old')
    accessed, modified = 1_600_000_000_000_000_000, 1_600_000_001_000_000_000
    os.utime(source, ns=(accessed, modified))
    sources = {'movie': [source]}
    with mock.patch('showco.provision.media_package.prepare_assets') as hash_assets:
        selected = inventory(sources, verify_media=False)
        source.write_bytes(b'new')
        os.utime(source, ns=(accessed + 10, modified))
        assert inventory(sources, verify_media=False).version == selected.version
        with stage_media(selected) as root:
            assert (root / 'movie/000/movie.mp4').read_bytes() == b'new'
            assert not (root / 'SHA256SUMS').exists()
        os.utime(source, ns=(accessed, modified + 1_000_000_000))
        assert inventory(sources, verify_media=False).version != selected.version
        with pytest.raises(ValueError, match='changed after preflight'):
            with stage_media(selected):
                pytest.fail('Changed media must not reach transfer')
        hash_assets.assert_not_called()
