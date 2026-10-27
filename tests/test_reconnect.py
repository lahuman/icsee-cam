import asyncio
from unittest.mock import Mock, patch

import pytest

from icsee_ptz.config import AuthenticationError, ProtocolError
from icsee_ptz.reconnect import Backoff, retryable
from icsee_ptz.web import MovementManager


def test_backoff_is_capped_and_reset():
    backoff = Backoff()
    assert [backoff.next() for _ in range(8)] == [2, 4, 8, 16, 30, 30, 30, 30]
    backoff.reset()
    assert backoff.next() == 2
    assert retryable(ConnectionError())
    assert retryable(TimeoutError())
    assert not retryable(AuthenticationError('denied'))
    assert not retryable(ProtocolError('invalid'))


def test_monitor_recovers_without_moving_or_replaying_commands():
    controller = Mock()
    factory = Mock(side_effect=[ConnectionError(), controller])
    manager = MovementManager(factory)
    delays = []
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError()
    with patch('icsee_ptz.web.asyncio.sleep', sleep):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(manager.monitor())
    assert delays == [2, 30]
    assert manager.connected is True
    assert manager.result is None
    controller.move.assert_not_called()
    controller.stop.assert_not_called()


def test_monitor_does_not_retry_bad_password():
    factory = Mock(side_effect=AuthenticationError('denied'))
    manager = MovementManager(factory)
    calls = []
    async def sleep(_):
        calls.append(1)
        if len(calls) == 3:
            raise asyncio.CancelledError()
    with patch('icsee_ptz.web.asyncio.sleep', sleep):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(manager.monitor())
    assert factory.call_count == 1
    assert manager.retry_blocked
