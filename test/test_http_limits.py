import threading
from unittest import mock

import pytest

from showco.runtime.server import CONNECTION_TIMEOUT_SECONDS, ShowcoServer


def test_excess_connections_are_closed_before_thread_creation() -> None:
    server = object.__new__(ShowcoServer)
    server.connection_slots = threading.BoundedSemaphore(1)
    server.connection_slots.acquire()
    request = mock.Mock()
    with (
        mock.patch.object(server, 'shutdown_request') as close,
        mock.patch('socketserver.ThreadingMixIn.process_request') as spawn,
    ):
        server.process_request(request, ('127.0.0.1', 1234))
    spawn.assert_not_called()
    close.assert_called_once_with(request)
    request.settimeout.assert_called_once_with(CONNECTION_TIMEOUT_SECONDS)


def test_thread_creation_failure_returns_connection_slot() -> None:
    server = object.__new__(ShowcoServer)
    server.connection_slots = threading.BoundedSemaphore(1)
    with (
        mock.patch(
            'socketserver.ThreadingMixIn.process_request', side_effect=RuntimeError
        ),
        pytest.raises(RuntimeError),
    ):
        server.process_request(mock.Mock(), ('127.0.0.1', 1234))
    assert server.connection_slots.acquire(blocking=False)


def test_finished_request_returns_connection_slot() -> None:
    server = object.__new__(ShowcoServer)
    server.connection_slots = threading.BoundedSemaphore(1)
    server.connection_slots.acquire()
    with mock.patch('socketserver.ThreadingMixIn.process_request_thread'):
        server.process_request_thread(mock.Mock(), ('127.0.0.1', 1234))
    assert server.connection_slots.acquire(blocking=False)
