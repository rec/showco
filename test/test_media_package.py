from pathlib import Path

import pytest
from reccy.runtime.asset_sets import verify_package

from showco.provision.media_package import inventory, stage_media


def test_version_depends_on_bytes_and_preserves_inbox(tmp_path: Path) -> None:
    photos = tmp_path / 'photos'
    photos.mkdir()
    (photos / 'one.jpg').write_bytes(b'image')
    (photos / 'incoming').mkdir()
    (photos / 'incoming/upload.jpg').write_bytes(b'live upload')
    selected = inventory({'photos': [photos]})
    assert selected.version == inventory({'photos': [photos]}).version
    with stage_media(selected) as root:
        verify_package(root, selected.manifest)
        assert (root / 'photos/000/one.jpg').read_bytes() == b'image'
        assert not (root / 'photos/000/incoming').exists()
    (photos / 'one.jpg').write_bytes(b'changed')
    assert selected.version != inventory({'photos': [photos]}).version
    with pytest.raises(ValueError, match='changed after preflight'):
        with stage_media(selected):
            pytest.fail('Changed media must not reach transfer')


def test_inventory_reports_all_missing_files(tmp_path: Path) -> None:
    from reccy.runtime.asset_sets import AssetPreparationError

    with pytest.raises(AssetPreparationError) as error:
        inventory({'photos': [tmp_path / 'first', tmp_path / 'second']})
    assert len(error.value.failures) == 2


def test_inventory_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / 'photos'
    source.mkdir()
    (source / 'outside').symlink_to(tmp_path / 'secret')
    with pytest.raises(ValueError, match='symlinks'):
        inventory({'photos': [source]})
