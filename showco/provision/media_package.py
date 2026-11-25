"""Content-identified media inventories and verified deployment staging."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import BinaryIO

import tomlkit
from pydantic import BaseModel
from reccy.runtime.asset_sets import (
    AssetFailure,
    AssetPreparationError,
    PackageManifest,
    prepare_assets,
    write_package,
)


class FileMetadata(BaseModel, frozen=True):
    size: int
    modified_ns: int
    accessed_ns: int


class MediaInventory(BaseModel, frozen=True):
    sources: dict[str, Path]
    metadata: dict[str, FileMetadata]
    directories: list[str]
    manifest: PackageManifest | None
    targets: dict[str, list[str]]

    @cached_property
    def version(self) -> str:
        if self.manifest is None:
            return self.metadata_version
        data = {
            'files': self.manifest.model_dump(mode='json'),
            'directories': self.directories,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    @cached_property
    def metadata_version(self) -> str:
        data = {
            'files': {n: [m.size, m.modified_ns] for n, m in self.metadata.items()},
            'directories': self.directories,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    @cached_property
    def manifest_bytes(self) -> bytes:
        if self.manifest is None:
            raise ValueError('Media hashes are disabled')
        return tomlkit.dumps(self.manifest.model_dump()).encode()

    @cached_property
    def checksum_bytes(self) -> bytes:
        if self.manifest is None:
            raise ValueError('Media hashes are disabled')
        checksums = {n: i.sha256 for n, i in self.manifest.files.items()}
        checksums['asset-manifest.toml'] = hashlib.sha256(
            self.manifest_bytes
        ).hexdigest()
        # GNU sha256sum uses escaped filenames for backslashes and newlines.
        lines = []
        for name, digest in checksums.items():
            escaped = name.replace('\\', '\\\\').replace('\n', '\\n')
            marker = '\\' if escaped != name else ''
            lines.append(f'{marker}{digest}  {escaped}\n')
        return ''.join(lines).encode()


def inventory(sources: dict[str, list[Path]], *, verify_media: bool) -> MediaInventory:
    files: dict[str, Path] = {}
    directories: list[str] = []
    targets: dict[str, list[str]] = {}
    failures: list[AssetFailure] = []
    for kind, selections in sources.items():
        targets[kind] = []
        for index, source in enumerate(selections):
            prefix = f'{kind}/{index:03d}'
            directories.append(prefix)
            if source.is_file():
                files[f'{prefix}/{source.name}'] = source
                targets[kind].append(f'{prefix}/{source.name}')
                continue
            targets[kind].append(prefix)
            if not source.is_dir():
                failures.append(AssetFailure(label=prefix, error='FileNotFoundError'))
                continue
            for path in sorted(source.rglob('*')):
                relative = path.relative_to(source)
                if kind == 'photos' and (
                    relative.parts[0] == 'incoming'
                    or relative.parts[0].startswith('.streamo-')
                ):
                    continue
                if path.is_symlink():
                    raise ValueError(f'Media packages cannot contain symlinks: {path}')
                if path.is_dir():
                    directories.append(f'{prefix}/{relative.as_posix()}')
                elif path.is_file():
                    files[f'{prefix}/{relative.as_posix()}'] = path
                else:
                    raise ValueError(
                        f'Media package member is not a regular file: {path}'
                    )
    if failures:
        raise AssetPreparationError(failures)
    metadata = {}
    for name, path in files.items():
        stat = path.stat()
        metadata[name] = FileMetadata(
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            accessed_ns=stat.st_atime_ns,
        )
    manifest = None
    if verify_media:
        with prepare_assets({n: open_source(p) for n, p in files.items()}) as prepared:
            manifest = PackageManifest(files=prepared.identities)
    return MediaInventory(
        sources=files,
        metadata=metadata,
        directories=sorted(directories),
        manifest=manifest,
        targets=targets,
    )


@contextmanager
def open_source(path: Path) -> Iterator[BinaryIO]:
    with path.open('rb') as stream:
        yield stream


@contextmanager
def stage_media(selected: MediaInventory) -> Iterator[Path]:
    """Copy the inventoried bytes; changing a selected source aborts deployment."""
    with TemporaryDirectory(prefix='showco-media-') as directory:
        root = Path(directory)
        for name in selected.directories:
            (root / name).mkdir(parents=True, exist_ok=True)
        if selected.manifest is not None:
            with prepare_assets(
                {n: open_source(p) for n, p in selected.sources.items()}
            ) as prepared:
                if prepared.identities != selected.manifest.files:
                    raise ValueError('Media source changed after preflight')
                write_package(root, prepared.streams, selected.manifest)
            (root / 'asset-manifest.toml').write_bytes(selected.manifest_bytes)
            (root / 'SHA256SUMS').write_bytes(selected.checksum_bytes)
        else:
            for name, source in selected.sources.items():
                metadata = selected.metadata[name]
                target = root / name
                before = source.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    metadata.size,
                    metadata.modified_ns,
                ):
                    raise ValueError(f'Media source changed after preflight: {source}')
                copyfile(source, target)
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (
                    before.st_size,
                    before.st_mtime_ns,
                ) or target.stat().st_size != metadata.size:
                    raise ValueError(f'Media source changed while staging: {source}')
        yield root
