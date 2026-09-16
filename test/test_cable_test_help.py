from __future__ import annotations

import sys
from unittest.mock import patch

from reccy.pytest_plugin import CliHelp

from showco.x18 import cable_test


def test_cable_test_help(cli_help: CliHelp) -> None:
    with patch.object(cable_test.machine_role, 'require_target_machine'):
        cli_help(
            'showco cable-test',
            lambda: cable_test.main(sys.argv[1:]),
        )
