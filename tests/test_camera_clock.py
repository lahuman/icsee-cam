from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

import pytest

from icsee_ptz.camera_clock import camera_clock, KST
from icsee_ptz.config import Config, ProtocolError
from icsee_ptz.verification import verified_profile


def setup():
    config = Config(password="clock-test-only")
    profile = verified_profile(config)
    client = MagicMock()
    client.__enter__.return_value = client
    client.read.return_value = {"HardWare": profile["hardware"], "SoftWareVersion": profile["firmware"]}
    client.camera_time.side_effect = lambda: datetime.now(KST).replace(tzinfo=None)
    return config, client


def test_clock_read_only_and_explicit_sync_use_current_kst():
    config, client = setup()
    result = camera_clock(config, factory=lambda: client)
    assert result["synchronized"] is False
    client.set_camera_time.assert_not_called()
    result = camera_clock(config, sync=True, factory=lambda: client)
    assert result["synchronized"] is True
    assert abs(result["offset_after_seconds"]) <= 2
    sent = client.set_camera_time.call_args.args[0]
    assert sent.tzinfo == KST
    assert abs((sent-datetime.now(KST)).total_seconds()) <= 2


def test_ack_without_matching_clock_is_failure():
    config, client = setup()
    client.camera_time.side_effect = lambda: (datetime.now(KST)-timedelta(minutes=2)).replace(tzinfo=None)
    with pytest.raises(ProtocolError, match="2초"):
        camera_clock(config, sync=True, factory=lambda: client)
    client.set_camera_time.assert_called_once()


def test_different_device_or_unverified_host_never_sets_clock():
    config, client = setup()
    client.read.return_value = {"HardWare": "other-camera"}
    with pytest.raises(ProtocolError):
        camera_clock(config, sync=True, factory=lambda: client)
    client.set_camera_time.assert_not_called()
    factory = Mock()
    with pytest.raises(ProtocolError):
        camera_clock(Config(host="192.168.1.169"), sync=True, factory=factory)
    factory.assert_not_called()
