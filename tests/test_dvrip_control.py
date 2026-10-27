from unittest.mock import Mock, patch
import time
import pytest
from icsee_ptz.dvrip_control import DVRIPControl


def controller():
    c=object.__new__(DVRIPControl)
    c.client=Mock()
    c.watchdog=Mock()
    return c


def test_low_speed_move_and_both_stop_connections():
    c=controller()
    r=c.move("right",0.01,0.2)
    c.client.ptz.assert_any_call("right",step=1)
    assert c.client.ptz.call_count==3  # stop, move, stop
    assert "finally" in r["stop_acknowledged_by"]


def test_error_attempts_final_stop():
    c=controller()
    c.client.ptz.side_effect=[None,TimeoutError(),None]
    r=c.move("up",0.01)
    assert r["move_response"]=="error"
    assert "finally" in r["stop_acknowledged_by"]


def test_independent_dvrip_watchdog_runs_during_delayed_response():
    c=controller()
    def operation(direction,**kwargs):
        if not kwargs.get("stop"):
            time.sleep(0.06)
            assert c.watchdog.ptz.call_count==2
    c.client.ptz.side_effect=operation
    assert "watchdog" in c.move("down",0.01)["stop_acknowledged_by"]


def test_stop_failure_blocks_dvrip_movement():
    c=controller()
    c.client.ptz.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):
        c.move("right",0.01)
    assert c.client.ptz.call_count==1


def test_three_seconds_requires_explicit_override():
    from icsee_ptz.config import CameraError
    c=controller()
    with pytest.raises(CameraError):
        c.move("left",3,0.25)
    c.client.ptz.assert_not_called()


def test_explicit_three_second_override_keeps_timer_and_legacy_shape():
    c=controller()
    with patch('icsee_ptz.dvrip_control.time.sleep'):
        result=c.move("left",3,0.25,max_duration=3,legacy_start=True)
    c.client.ptz.assert_any_call("left",step=2,legacy_start=True)
    assert result["duration"]==3
    assert "finally" in result["stop_acknowledged_by"]


def test_early_release_primary_stop_failure_uses_independent_connection():
    import threading
    c = controller()
    released = threading.Event()
    def command(direction, **kwargs):
        if not kwargs.get('stop'):
            released.set()
        elif released.is_set():
            raise TimeoutError()
    c.client.ptz.side_effect = command
    result = c.move('left', 2, cancel_event=released)
    assert result['stop_acknowledged_by'] == ['watchdog_retry']
    assert result['stop_errors'][0]['source'] == 'finally'
    assert c.watchdog.ptz.call_count == 2  # preflight, independent fallback


def test_all_stop_failures_remain_visible_to_status_consumer():
    import threading
    c = controller()
    released = threading.Event()
    def primary(direction, **kwargs):
        if not kwargs.get('stop'):
            released.set()
        elif released.is_set():
            raise TimeoutError()
    c.client.ptz.side_effect = primary
    c.watchdog.ptz.side_effect = [None, TimeoutError()]
    result = c.move('up', 2, cancel_event=released)
    assert result['stop_acknowledged_by'] == []
    assert {item['source'] for item in result['stop_errors']} == {'finally', 'watchdog_retry'}
