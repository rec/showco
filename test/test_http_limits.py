import threading
from unittest import mock

import pytest

from showco.runtime.server import (
    ACTION_BODY_TIMEOUT_SECONDS,
    CONNECTION_TIMEOUT_SECONDS,
    ShowcoHandler,
    ShowcoServer,
)


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


@pytest.mark.parametrize(
    'headers',
    [
        {'Host': 'show.local', 'Origin': 'https://other.example'},
        {'Host': 'show.local', 'Origin': 'null'},
        {'Host': 'show.local', 'Origin': 'http://[invalid'},
        {'Host': 'show.local', 'Sec-Fetch-Site': 'cross-site'},
    ],
)
def test_cross_origin_actions_are_rejected_before_reading_body(
    headers: dict[str, str],
) -> None:
    handler = object.__new__(ShowcoHandler)
    handler.path = '/actions'
    handler.headers = headers
    with (
        mock.patch.object(handler, 'send_error') as error,
        mock.patch.object(handler, '_form') as form,
    ):
        handler._do_post()
    assert error.call_args.args[0] == 403
    form.assert_not_called()


def test_status_request_uses_reserved_slot_when_ordinary_slots_are_busy() -> None:
    handler = object.__new__(ShowcoHandler)
    handler.path = '/status'
    handler.server = mock.Mock(
        request_slots=threading.BoundedSemaphore(1),
        status_slots=threading.BoundedSemaphore(1),
    )
    handler.server.request_slots.acquire()
    with mock.patch.object(handler, '_do_get') as get:
        handler.do_GET()

    get.assert_called_once_with()
    assert handler.server.status_slots.acquire(blocking=False)


def test_slow_and_truncated_action_bodies_return_clear_errors() -> None:
    handler = object.__new__(ShowcoHandler)
    handler.headers = {'Content-Length': '7'}
    handler.connection = mock.Mock()
    handler.rfile = mock.Mock()
    handler.rfile.read.side_effect = TimeoutError
    with pytest.raises(ValueError, match='timed out'):
        handler._form()
    handler.connection.settimeout.assert_any_call(ACTION_BODY_TIMEOUT_SECONDS)
    handler.connection.settimeout.assert_any_call(CONNECTION_TIMEOUT_SECONDS)

    handler.rfile.read.side_effect = None
    handler.rfile.read.return_value = b'action'
    with pytest.raises(ValueError, match='incomplete'):
        handler._form()
