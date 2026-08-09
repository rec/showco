from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from showco import machine_role


class MachineRoleTests(unittest.TestCase):
    def test_mark_target_machine_writes_role_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "machine-role"
            with mock.patch.dict(
                os.environ,
                {machine_role.ROLE_FILE_ENVIRONMENT_VARIABLE: str(path)},
            ):
                machine_role.mark_target_machine()

                self.assertEqual(machine_role.machine_role(), machine_role.TARGET_ROLE)

    def test_target_commands_require_target_marker(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "machine-role"
            with mock.patch.dict(
                os.environ,
                {machine_role.ROLE_FILE_ENVIRONMENT_VARIABLE: str(path)},
            ):
                with self.assertRaisesRegex(SystemExit, "target machine"):
                    machine_role.require_target_machine("showco run")

    def test_provisioning_commands_refuse_target_marker(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "machine-role"
            with mock.patch.dict(
                os.environ,
                {machine_role.ROLE_FILE_ENVIRONMENT_VARIABLE: str(path)},
            ):
                machine_role.mark_target_machine()

                with self.assertRaisesRegex(SystemExit, "provisioning machine"):
                    machine_role.require_provisioning_machine("showco provision")


if __name__ == "__main__":
    unittest.main()
