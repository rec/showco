"""Check external Wi-Fi and explicitly activate saved profiles at startup."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel
from reccy.runtime import logging

from ..provision.network import split_nmcli_terse_fields
from .models import NetworkStatus

LOGGER = logging.get_logger(__name__)
STARTUP_SECONDS = 30.0
STATUS_SECONDS = 5.0
COMMAND_SECONDS = 3.0


class WifiProfile(BaseModel, frozen=True):
    name: str
    uuid: str
    interface: str
    priority: int


class ExternalWifi:
    def __init__(
        self,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_command = run_command
        self.clock = clock
        self.profiles: list[WifiProfile] = []
        self.current: NetworkStatus | None = None
        self.checked_at = float('-inf')
        self.lock = threading.Lock()

    def connect(self) -> None:
        """Make one bounded startup pass; later status checks never reconnect."""
        deadline = self.clock() + STARTUP_SECONDS
        failures: list[str] = []
        with self.lock:
            self.profiles = []
            self.current = None
            try:
                inventory = self._command(
                    ['nmcli', '-t', '-f', 'NAME,UUID', 'connection', 'show'], deadline
                )
                for line in inventory.splitlines():
                    fields = split_nmcli_terse_fields(line)
                    if len(fields) != 2 or not fields[0].startswith('showco-external-'):
                        continue
                    details = self._command(
                        [
                            'nmcli',
                            '-g',
                            'connection.interface-name,connection.autoconnect-priority',
                            'connection',
                            'show',
                            'uuid',
                            fields[1],
                        ],
                        deadline,
                    ).splitlines()
                    if len(details) != 2 or not details[0]:
                        raise ValueError(f'Invalid Wi-Fi profile: {fields[0]}')
                    self.profiles.append(
                        WifiProfile(
                            name=fields[0],
                            uuid=fields[1],
                            interface=details[0],
                            priority=int(details[1]),
                        )
                    )
                self.profiles.sort(key=lambda p: (-p.priority, p.name))
                if not self.profiles:
                    return
                for profile in self.profiles:
                    connected, protected = self._connection(deadline)
                    if connected:
                        self.current = NetworkStatus(connected=True, message=connected)
                        return
                    if profile.interface in protected:
                        failures.append(f'{profile.name}: adapter is hosting a hotspot')
                        continue
                    try:
                        remaining = deadline - self.clock()
                        if remaining <= 1:
                            raise subprocess.TimeoutExpired(['nmcli'], STARTUP_SECONDS)
                        wait = max(1, min(10, int(remaining - 1)))
                        LOGGER.info('Trying external Wi-Fi profile %s', profile.name)
                        self._command(
                            [
                                'sudo',
                                '-n',
                                'nmcli',
                                '--wait',
                                str(wait),
                                'connection',
                                'up',
                                'uuid',
                                profile.uuid,
                            ],
                            deadline,
                            timeout=wait + 1,
                        )
                    except (OSError, subprocess.SubprocessError, ValueError) as error:
                        failures.append(f'{profile.name}: {error}')
                connected, _ = self._connection(deadline)
                if connected:
                    self.current = NetworkStatus(connected=True, message=connected)
                else:
                    self.current = NetworkStatus(
                        message='External Wi-Fi connection failed. '
                        + '; '.join(failures)
                        if failures
                        else 'NetworkManager did not activate external Wi-Fi'
                    )
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self.current = NetworkStatus(
                    message=f'External Wi-Fi check failed: {error}'
                )
            finally:
                self.checked_at = self.clock()
                if self.current is not None and not self.current.connected:
                    LOGGER.error('%s', self.current.message)

    def status(self) -> NetworkStatus | None:
        with self.lock:
            if not self.profiles or self.clock() - self.checked_at < STATUS_SECONDS:
                return self.current
            self.checked_at = self.clock()
            try:
                connected, _ = self._connection(self.clock() + COMMAND_SECONDS)
                if connected:
                    self.current = NetworkStatus(connected=True, message=connected)
                elif self.current is None or self.current.connected:
                    self.current = NetworkStatus(
                        message='External Wi-Fi is disconnected'
                    )
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self.current = NetworkStatus(
                    message=f'External Wi-Fi check failed: {error}'
                )
            return self.current

    def _connection(self, deadline: float) -> tuple[str | None, set[str]]:
        active = self._command(
            [
                'nmcli',
                '-t',
                '-f',
                'UUID,TYPE,DEVICE,STATE',
                'connection',
                'show',
                '--active',
            ],
            deadline,
        )
        interfaces = {p.interface for p in self.profiles}
        connected = None
        protected: set[str] = set()
        for line in active.splitlines():
            fields = split_nmcli_terse_fields(line)
            if (
                len(fields) != 4
                or fields[1] not in {'wifi', '802-11-wireless'}
                or fields[2] not in interfaces
            ):
                continue
            mode = self._command(
                [
                    'nmcli',
                    '-g',
                    '802-11-wireless.mode',
                    'connection',
                    'show',
                    'uuid',
                    fields[0],
                ],
                deadline,
            ).strip()
            if mode == 'ap':
                protected.add(fields[2])
            elif mode == 'infrastructure' and fields[3] == 'activated':
                connected = f'External Wi-Fi connected on {fields[2]}'
        return connected, protected

    def _command(
        self, command: list[str], deadline: float, timeout: float = COMMAND_SECONDS
    ) -> str:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, STARTUP_SECONDS)
        result = self.run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(timeout, remaining),
            env={**os.environ, 'LC_ALL': 'C'},
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'NetworkManager command failed')
        return result.stdout
