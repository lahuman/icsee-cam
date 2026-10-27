from datetime import timedelta
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
import time
import pytest
from icsee_ptz.config import CameraError, Config, ProtocolError
from icsee_ptz.control import ONVIFControl
from icsee_ptz.onvif_client import advertised_endpoint


def controller():
    c = object.__new__(ONVIFControl)
    c.profile = {"profile_token": "p", "options": {
        "Spaces": {"ContinuousPanTiltVelocitySpace": [
            {"URI": "velocity", "XRange": {"Min": -1, "Max": 1},
             "YRange": {"Min": -1, "Max": 1}}]},
        "PTZTimeout": {"Min": timedelta(seconds=1), "Max": timedelta(seconds=100)}}}
    c.ptz = Mock()
    c.watchdog_ptz = Mock()
    return c


def test_wrong_advertised_ip_is_pinned_to_real_camera():
    assert advertised_endpoint("http://192.168.1.10:8899/onvif/ptz_service",
        "192.168.1.168", 8899) == "http://192.168.1.168:8899/onvif/ptz_service"


@pytest.mark.parametrize("url", ["http://8.8.8.8:8899/onvif/ptz_service",
    "http://192.168.1.10:80/onvif/ptz_service", "http://192.168.1.10:8899/other",
    "http://admin:secret@192.168.1.10:8899/onvif/ptz_service"])
def test_bad_advertisements_are_rejected(url):
    with pytest.raises(CameraError):
        advertised_endpoint(url, "192.168.1.168", 8899)


@pytest.mark.parametrize("direction,xy", [("left",(-0.2,0)),("right",(0.2,0)),
                                        ("up",(0,0.2)),("down",(0,-0.2))])
def test_direction_and_final_stop(direction, xy):
    c=controller()
    result=c.move(direction,0.01,0.2)
    velocity=c.ptz.ContinuousMove.call_args.kwargs["Velocity"]["PanTilt"]
    assert (velocity["x"],velocity["y"]) == xy
    assert c.ptz.ContinuousMove.call_args.kwargs["Timeout"] == timedelta(seconds=1)
    assert c.ptz.Stop.call_count == 2  # preflight + finally
    assert result["stop_acknowledged_by"]
    assert result["physical_motion_verified"] is False


def test_move_error_still_sends_stop():
    c=controller()
    c.ptz.ContinuousMove.side_effect=TimeoutError()
    result=c.move("right",0.01)
    assert c.ptz.Stop.call_count == 2
    assert result["move_response"] == "error"
    assert "finally" in result["stop_acknowledged_by"]


def test_independent_watchdog_stops_while_move_response_blocks():
    c=controller()
    state=[]
    def slow_move(**_):
        time.sleep(0.06)
        assert state == ["stop"]
    c.ptz.ContinuousMove.side_effect=slow_move
    c.watchdog_ptz.Stop.side_effect=lambda **_: state.append("stop")
    assert "watchdog" in c.move("up",0.01)["stop_acknowledged_by"]


def test_stop_failure_prevents_movement():
    c=controller()
    c.ptz.Stop.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):
        c.move("up",0.01)
    c.ptz.ContinuousMove.assert_not_called()


def test_final_stop_failure_is_reported():
    c=controller()
    c.ptz.Stop.side_effect=[None, TimeoutError()]
    result=c.move("down",0.01)
    assert result["stop_errors"][0]["kind"] == "timeout"


@pytest.mark.parametrize("duration,speed", [(3,0.2),(float('nan'),0.2),(0.5,0),(0.5,float('inf'))])
def test_invalid_input_never_moves(duration,speed):
    c=controller()
    with pytest.raises(CameraError):
        c.move("right",duration,speed)
    c.ptz.ContinuousMove.assert_not_called()


def test_missing_capabilities_never_enable_control():
    with patch("icsee_ptz.control.ONVIFProbe") as probe:
        probe.return_value.probe.return_value={"endpoint_confirmed":True,"profiles":[]}
        with pytest.raises(ProtocolError):
            ONVIFControl(Config(password="test-only"))
        probe.return_value.service.assert_not_called()
