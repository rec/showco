import subprocess
from unittest.mock import Mock, patch

import pytest

from showco.runtime import gui_schema, models, rehearsal, server, views
from showco.runtime.app import ShowcoApp
from showco.runtime.network import ExternalWifi


class NetworkManager:
    def __init__(self) -> None:
        self.profiles = {'home': ('wlan0', 999), 'venue': ('wlan0', 998)}
        self.active: list[tuple[str, str]] = []
        self.states: dict[str, str] = {}
        self.modes: dict[str, str] = {'private': 'ap', 'existing': 'infrastructure'}
        self.failures: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.now = 0.0
        self.command_seconds = 0.0

    def run(
        self, command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        timeout = float(str(options['timeout']))
        self.timeouts.append(timeout)
        self.now += min(self.command_seconds, timeout)
        if self.command_seconds >= timeout:
            raise subprocess.TimeoutExpired(command, timeout)
        if command[1:4] == ['-t', '-f', 'NAME,UUID']:
            output = ''.join(f'showco-external-{k}:{k}\n' for k in self.profiles)
        elif 'connection.interface-name,connection.autoconnect-priority' in command:
            interface, priority = self.profiles[command[-1]]
            output = f'{interface}\n{priority}\n'
        elif command[-1] == '--active':
            output = ''.join(
                f'{u}:wifi:{i}:{self.states.get(u, "activated")}\n'
                for u, i in self.active
            )
        elif command[2] == '802-11-wireless.mode':
            output = self.modes[command[-1]] + '\n'
        elif 'up' in command:
            uuid = command[-1]
            if error := self.failures.get(uuid):
                return subprocess.CompletedProcess(command, 10, '', error)
            self.active.append((uuid, self.profiles[uuid][0]))
            self.modes[uuid] = 'infrastructure'
            output = 'Connection successfully activated\n'
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, output, '')

    def clock(self) -> float:
        return self.now


def test_existing_external_connection_is_preserved() -> None:
    nm = NetworkManager()
    nm.active = [('existing', 'wlan0'), ('private', 'wlan1')]
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert wifi.status().connected
    assert not any('up' in c for c in nm.calls)


def test_disconnected_wifi_tries_profiles_in_priority_order() -> None:
    nm = NetworkManager()
    nm.profiles = dict(reversed(list(nm.profiles.items())))
    nm.active = [('private', 'wlan1')]
    nm.failures['home'] = 'SSID not found'
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert wifi.status().connected
    assert [c[-1] for c in nm.calls if 'up' in c] == ['home', 'venue']
    assert all(c[:3] == ['sudo', '-n', 'nmcli'] for c in nm.calls if 'up' in c)
    assert not any('down' in c or 'modify' in c for c in nm.calls)


def test_network_failure_is_visible_in_front_page_faults_and_health() -> None:
    nm = NetworkManager()
    nm.failures = {'home': 'SSID not found', 'venue': 'Authentication failed'}
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    app = ShowcoApp(
        rehearsal.RehearsalRecsClient(),
        None,
        rehearsal.RehearsalSystemMonitor(),
        rehearsal.RehearsalMixersMonitor(),
        external_wifi=wifi,
    )
    status = app.status()
    fault = next(f for f in status.active_faults if f.name == 'External Wi-Fi')
    assert 'Authentication failed' in fault.message
    assert not status.readiness.ready
    html = views.configured_page(gui_schema.current_gui().page('health'), status)
    assert 'External Wi-Fi' in html
    assert 'Authentication failed' in html


def test_later_connection_clears_fault_without_more_activation_attempts() -> None:
    nm = NetworkManager()
    nm.failures = {'home': 'not found', 'venue': 'not found'}
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert not wifi.status().connected
    nm.active = [('existing', 'wlan0')]
    nm.now += 6
    assert wifi.status().connected
    assert len([c for c in nm.calls if 'up' in c]) == 2
    nm.active = []
    nm.now += 6
    assert not wifi.status().connected
    assert len([c for c in nm.calls if 'up' in c]) == 2


def test_hotspot_on_external_adapter_is_never_replaced() -> None:
    nm = NetworkManager()
    nm.active = [('private', 'wlan0')]
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert not wifi.status().connected
    assert 'hosting a hotspot' in wifi.status().message
    assert not any('up' in c for c in nm.calls)


def test_private_only_installations_do_not_require_external_wifi() -> None:
    nm = NetworkManager()
    nm.profiles = {}
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert wifi.status() is None
    assert not any('up' in c for c in nm.calls)


@pytest.mark.parametrize(
    'failure',
    [FileNotFoundError('nmcli missing'), subprocess.TimeoutExpired(['nmcli'], 3)],
)
def test_network_manager_failure_is_reported(failure: Exception) -> None:
    wifi = ExternalWifi(Mock(side_effect=failure))
    wifi.connect()
    assert not wifi.status().connected
    assert 'External Wi-Fi check failed' in wifi.status().message


def test_startup_has_one_total_deadline() -> None:
    nm = NetworkManager()
    nm.command_seconds = 2
    nm.failures = {'home': 'not found', 'venue': 'not found'}
    for i in range(20):
        nm.profiles[str(i)] = ('wlan0', i)
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert nm.now <= 30
    assert not wifi.status().connected
    assert max(nm.timeouts) <= 3


def test_activation_timeout_can_fall_back_to_another_profile() -> None:
    nm = NetworkManager()

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if 'up' in command and command[-1] == 'home':
            nm.calls.append(command)
            raise subprocess.TimeoutExpired(command, 11)
        return nm.run(command, **options)

    wifi = ExternalWifi(run, nm.clock)
    wifi.connect()
    assert wifi.status().connected
    assert [c[-1] for c in nm.calls if 'up' in c] == ['home', 'venue']


def test_activation_success_is_not_enough_without_an_active_connection() -> None:
    nm = NetworkManager()

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if 'up' in command:
            return subprocess.CompletedProcess(command, 0, 'activated', '')
        return nm.run(command, **options)

    wifi = ExternalWifi(run, nm.clock)
    wifi.connect()
    assert not wifi.status().connected
    assert 'did not activate' in wifi.status().message


def test_dhcp_pending_does_not_count_as_connected() -> None:
    nm = NetworkManager()
    nm.active = [('existing', 'wlan0')]
    nm.states['existing'] = 'activating'
    nm.failures = {'home': 'DHCP failed', 'venue': 'SSID not found'}
    wifi = ExternalWifi(nm.run, nm.clock)
    wifi.connect()
    assert not wifi.status().connected
    assert [c[-1] for c in nm.calls if 'up' in c] == ['home', 'venue']


def test_server_checks_startup_network_without_blocking_on_failure() -> None:
    wifi = Mock(spec=ExternalWifi)
    wifi.status.return_value = models.NetworkStatus(message='venue unavailable')
    with patch.object(server, 'ShowcoServer'):
        result = server.make_server(
            'localhost',
            0,
            recs=rehearsal.RehearsalRecsClient(),
            system=rehearsal.RehearsalSystemMonitor(),
            external_wifi=wifi,
        )
    wifi.connect.assert_called_once_with()
    assert result.app.status().network.message == 'venue unavailable'
