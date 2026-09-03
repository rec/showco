from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from showco import go, update
from showco.provision import provision


class GoTests(unittest.TestCase):
    def options(self, *, system: bool = False, **kwargs: object) -> provision.GoOptions:
        return provision.GoOptions(
            config_path=Path("config.toml"),
            secrets=Path("secrets.toml"),
            system=system,
            **kwargs,
        )

    def test_main_prints_completion_after_success(self) -> None:
        options = self.options()
        with (
            mock.patch("showco.go.tyro.cli", return_value=options),
            mock.patch("showco.go.run", return_value=0),
            mock.patch("builtins.print") as print_message,
        ):
            result = go.main([])

        self.assertEqual(result, 0)
        print_message.assert_called_once_with("Successfully completed")

    def test_main_does_not_print_completion_after_failure(self) -> None:
        options = self.options()
        with (
            mock.patch("showco.go.tyro.cli", return_value=options),
            mock.patch("showco.go.run", return_value=1),
            mock.patch("builtins.print") as print_message,
        ):
            result = go.main([])

        self.assertEqual(result, 1)
        print_message.assert_not_called()

    def test_matching_configuration_updates_target(self) -> None:
        provision_config = mock.Mock(ssh_target="tom@bertrand.local")
        with (
            mock.patch(
                "showco.provision.provision.resolved_config",
                return_value=provision_config,
            ),
            mock.patch(
                "showco.provision.state.provisioning_fingerprint",
                return_value="fingerprint",
            ),
            mock.patch(
                "showco.provision.remote.applied_provisioning_fingerprint",
                return_value="fingerprint",
            ),
            mock.patch(
                "showco.update.update_from_provisioning_machine", return_value=0
            ) as update_target,
            mock.patch("showco.provision.provision.run") as provision_target,
        ):
            result = go.run(self.options())

        self.assertEqual(result, 0)
        update_target.assert_called_once()
        self.assertEqual(update_target.call_args.args[0], update.REPOSITORY_NAMES)
        self.assertIs(update_target.call_args.kwargs["target_config"], provision_config)
        provision_target.assert_not_called()

    def test_missing_applied_state_provisions_target(self) -> None:
        provision_config = mock.Mock(ssh_target="tom@bertrand.local")
        with (
            mock.patch(
                "showco.provision.provision.resolved_config",
                return_value=provision_config,
            ),
            mock.patch(
                "showco.provision.state.provisioning_fingerprint",
                return_value="fingerprint",
            ),
            mock.patch(
                "showco.provision.remote.applied_provisioning_fingerprint",
                return_value=None,
            ),
            mock.patch(
                "showco.provision.provision.run", return_value=0
            ) as provision_target,
            mock.patch(
                "showco.update.update_from_provisioning_machine"
            ) as update_target,
        ):
            result = go.run(self.options())

        self.assertEqual(result, 0)
        provision_target.assert_called_once_with(
            self.options(), provision_config=provision_config
        )
        update_target.assert_not_called()

    def test_system_option_forces_provisioning(self) -> None:
        provision_config = mock.Mock(ssh_target="tom@bertrand.local")
        with (
            mock.patch(
                "showco.provision.provision.resolved_config",
                return_value=provision_config,
            ),
            mock.patch(
                "showco.provision.remote.applied_provisioning_fingerprint"
            ) as applied,
            mock.patch(
                "showco.provision.provision.run", return_value=0
            ) as provision_target,
        ):
            result = go.run(self.options(system=True))

        self.assertEqual(result, 0)
        applied.assert_not_called()
        provision_target.assert_called_once_with(
            self.options(system=True), provision_config=provision_config
        )

    def test_selected_repositories_update_without_provisioning(self) -> None:
        provision_config = mock.Mock(ssh_target="tom@bertrand.local")
        options = self.options(repositories=["recs"])
        with (
            mock.patch(
                "showco.provision.provision.resolved_config",
                return_value=provision_config,
            ),
            mock.patch(
                "showco.update.update_from_provisioning_machine", return_value=0
            ) as update_target,
            mock.patch("showco.provision.provision.run") as provision_target,
        ):
            result = go.run(options)

        self.assertEqual(result, 0)
        update_target.assert_called_once_with(
            ["recs"],
            host=None,
            root=None,
            target_config=provision_config,
            output=mock.ANY,
            autosquash=50,
        )
        provision_target.assert_not_called()

    def test_remote_update_skips_local_repositories(self) -> None:
        provision_config = mock.Mock(ssh_target="tom@bertrand.local")
        options = self.options(remote=True, repositories=["recs"])
        with (
            mock.patch(
                "showco.provision.provision.resolved_config",
                return_value=provision_config,
            ),
            mock.patch("showco.update.update_remote_target", return_value=0) as remote,
            mock.patch("showco.update.update_from_provisioning_machine") as local,
        ):
            result = go.run(options)

        self.assertEqual(result, 0)
        remote.assert_called_once_with(
            ["recs"],
            host=None,
            root=None,
            target_config=provision_config,
            output=mock.ANY,
        )
        local.assert_not_called()

    def test_target_machine_updates_selected_repositories(self) -> None:
        with mock.patch("showco.update.update_target", return_value=0) as update_target:
            result = go.run(self.options(target_machine=True, repositories=["recs"]))

        self.assertEqual(result, 0)
        update_target.assert_called_once_with(["recs"], root=None)

    def test_system_cannot_be_combined_with_update_options(self) -> None:
        with self.assertRaisesRegex(SystemExit, "cannot be combined"):
            go.run(self.options(system=True, repositories=["recs"]))

    def test_remote_is_unavailable_on_target_machine(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unavailable on the target"):
            go.run(self.options(target_machine=True, remote=True))
