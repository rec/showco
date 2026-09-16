from threading import Event
from unittest import mock

import pytest
from reccy.protocol import rpc

from showco.runtime import waveforms
from showco.runtime.recs_control import RecsControlClient


@pytest.mark.parametrize('ending', ['transport_close', 'shutdown', 'invalid_event'])
def test_waveforms_reconnect_with_a_fresh_subscription(ending: str) -> None:
    closed = [Event(), Event()]
    subscribed = [Event(), Event()]
    clients = [mock.Mock(spec=rpc.EventClient) for _ in closed]
    for client, signal in zip(clients, closed, strict=True):
        client.wait_closed.side_effect = signal.wait
        client.close.side_effect = signal.set
    factory = mock.Mock(side_effect=clients)
    control = mock.Mock(spec=RecsControlClient)
    subscriptions = 0

    def command(name: str, *, timeout: float = 6.0) -> object:
        nonlocal subscriptions
        if name == 'subscribe_waveforms':
            subscribed[subscriptions].set()
            subscriptions += 1
            return {'type': 'waveform_subscription', 'active': True}
        return {'type': 'waveform_subscription', 'active': False}

    control.call.side_effect = command
    bridge = waveforms.WaveformBridge(event_client=factory, control=control)
    # Keep the same interruptible delay mechanism without slowing the test.
    with mock.patch.object(waveforms, 'WAVEFORM_RECONNECT_SECONDS', 0.01):
        bridge.start()
        try:
            assert subscribed[0].wait(2)
            if ending == 'transport_close':
                closed[0].set()
            elif ending == 'shutdown':
                bridge.receive(rpc.Event(name='shutdown', data={}))
            else:
                bridge.receive(rpc.Event(name='waveform_layout', data={}))
            assert subscribed[1].wait(2)
        finally:
            bridge.close()
    assert factory.call_count == 2
    for client in clients:
        client.start.assert_called_once_with()
        client.close.assert_called_once_with()
    assert bridge.thread is not None and not bridge.thread.is_alive()
    control.call.assert_any_call('unsubscribe_waveforms', timeout=1.0)


def test_waveform_stop_interrupts_wait_for_an_open_connection() -> None:
    waiting = Event()
    client = mock.Mock(spec=rpc.EventClient)
    never_closed = Event()

    def wait_closed(timeout: float) -> bool:
        waiting.set()
        return never_closed.wait(timeout)

    client.wait_closed.side_effect = wait_closed
    control = mock.Mock(spec=RecsControlClient)
    control.call.return_value = {'type': 'waveform_subscription', 'active': True}
    bridge = waveforms.WaveformBridge(
        event_client=lambda receive: client, control=control
    )
    bridge.start()
    try:
        assert waiting.wait(2)
    finally:
        bridge.close()
    assert bridge.thread is not None and not bridge.thread.is_alive()
    client.close.assert_called_once_with()
    assert control.call.call_count == 2


def test_inactive_waveform_subscription_is_closed_without_waiting() -> None:
    client = mock.Mock(spec=rpc.EventClient)
    control = mock.Mock(spec=RecsControlClient)
    control.call.return_value = {'type': 'waveform_subscription', 'active': False}
    bridge = waveforms.WaveformBridge(
        event_client=lambda receive: client, control=control
    )
    retrying = Event()

    def retry_wait(timeout: float) -> bool:
        assert timeout == waveforms.WAVEFORM_RECONNECT_SECONDS
        retrying.set()
        return Event.wait(bridge.stopped, timeout)

    with mock.patch.object(bridge.stopped, 'wait', side_effect=retry_wait):
        bridge.start()
        try:
            assert retrying.wait(2)
        finally:
            bridge.close()
    client.wait_closed.assert_not_called()
    client.close.assert_called_once_with()
    assert bridge.thread is not None and not bridge.thread.is_alive()
