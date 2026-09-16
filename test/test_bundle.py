from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from showco.deployment.bundle import create_bundle


class BundleTests(unittest.TestCase):
    def test_two_bundles_at_the_same_time_have_separate_destinations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destinations = [
                create_bundle(
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

            destination = create_bundle(
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
