from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from showco.deployment.bundle import create_bundle


class BundleTests(unittest.TestCase):
    def test_excludes_secrets_and_includes_recording_journal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            config = root / "config"
            journal = root / "recordings/session-record.jsonl"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}\n")
            (state / "recs").mkdir(parents=True)
            (state / "recs/status.json").write_text(
                json.dumps({"record_path": str(journal)})
            )
            (state / "showco").mkdir(parents=True)
            (state / "showco/showco.log").write_text("showco\n")
            config.mkdir()
            (config / "config.toml").write_text("[network]\n")
            (config / "secrets.toml").write_text("secret = 'no'\n")

            destination = create_bundle(
                root / "bundles",
                state_directory=state,
                config_directory=config,
                now=datetime(2026, 9, 10, tzinfo=timezone.utc),
            )

            files = json.loads((destination / "bundle.json").read_text())["files"]
            self.assertTrue(
                any(path.endswith("session-record.jsonl") for path in files)
            )
            self.assertFalse(any("secrets" in path for path in files))
