from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from showco.deployment import bundle


class BundleTests(unittest.TestCase):
    def test_two_bundles_at_the_same_time_have_separate_destinations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destinations = [
                bundle.create_bundle(
                    root / 'bundles',
                    state_directory=root / 'state',
                    config_directory=root / 'config',
                    now=datetime(2026, 9, 10, tzinfo=timezone.utc),
                )
                for _ in range(2)
            ]
            self.assertNotEqual(*destinations)
            self.assertTrue(all((p / 'bundle.json').is_file() for p in destinations))

    def test_excludes_secrets_and_includes_recording_journal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            config = root / 'config'
            journal = root / 'recordings/session-record.jsonl'
            journal.parent.mkdir(parents=True)
            journal.write_text('{}\n')
            (state / 'recs').mkdir(parents=True)
            (state / 'recs/status.json').write_text(
                json.dumps({'record_path': str(journal)})
            )
            (state / 'showco').mkdir(parents=True)
            (state / 'showco/showco.log').write_text('showco\n')
            (state / 'showco/incidents.json').write_text('{}')
            (state / 'showco/recovery.json').write_text('{}')
            (state / 'showco/setlist.json').write_text('private notes')
            config.mkdir()
            (config / 'config.toml').write_text('[network]\n')
            (config / 'secrets.toml').write_text("secret = 'no'\n")

            destination = bundle.create_bundle(
                root / 'bundles',
                state_directory=state,
                config_directory=config,
                now=datetime(2026, 9, 10, tzinfo=timezone.utc),
            )

            files = json.loads((destination / 'bundle.json').read_text())['files']
            self.assertTrue(
                any(path.endswith('session-record.jsonl') for path in files)
            )
            self.assertFalse(any('secrets' in path for path in files))
            self.assertIn('state/showco/incidents.json', files)
            self.assertIn('state/showco/recovery.json', files)
            self.assertFalse(any('setlist' in path for path in files))

    def test_bundle_caps_files_and_total_size_and_records_truncation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            config = root / 'config'
            journal = root / 'recordings/session.jsonl'
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b'j' * 299 + b'Z')
            (state / 'recs').mkdir(parents=True)
            (state / 'recs/status.json').write_text(
                json.dumps({'record_path': str(journal)})
            )
            (state / 'showco').mkdir(parents=True)
            (state / 'showco/showco.log').write_bytes(b'l' * 299 + b'Z')
            config.mkdir()
            (config / 'config.toml').write_bytes(b'c' * 300)

            with (
                mock.patch.object(bundle, 'MAX_FILE_BYTES', 256),
                mock.patch.object(bundle, 'MAX_BUNDLE_BYTES', 600),
            ):
                destination = bundle.create_bundle(
                    root / 'bundles', state_directory=state, config_directory=config
                )

            manifest = json.loads((destination / 'bundle.json').read_text())
            self.assertEqual((destination / 'config/config.toml').stat().st_size, 256)
            self.assertEqual(
                (destination / 'recordings/session.jsonl').read_bytes(),
                b'j' * 255 + b'Z',
            )
            self.assertEqual(
                (destination / 'state/showco/showco.log').stat().st_size, 88
            )
            self.assertEqual(
                sum((destination / f).stat().st_size for f in manifest['files']), 600
            )
            self.assertIn('recordings/session.jsonl', manifest['truncated'])
            self.assertIn('state/recs/status.json', manifest['omitted'])
