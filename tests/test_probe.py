from unittest.mock import patch
from icsee_ptz.config import Config
from icsee_ptz.probe import probe


def test_unreachable_does_not_attempt_login_or_service_calls():
    with patch("icsee_ptz.probe.tcp_probe", side_effect=lambda c, p:
               {"port": p, "tcp": "failed", "kind": "connection", "errno": 65}), \
         patch("icsee_ptz.probe.probe_onvif") as onvif, \
         patch("icsee_ptz.probe.DVRIPClient") as dvrip:
        result = probe(Config(password="test-only"))
    onvif.assert_not_called()
    dvrip.assert_not_called()
    assert result["active_protocol"] is None
    assert "LAN" in result["next_step"]


def test_onvif_before_dvrip_and_no_auto_activation():
    order = []
    with patch("icsee_ptz.probe.tcp_probe", side_effect=lambda c, p: {"port": p, "tcp": "open"}), \
         patch("icsee_ptz.probe.probe_onvif", side_effect=lambda *a:
               order.append("onvif") or [{"endpoint_confirmed": True}]), \
         patch("icsee_ptz.probe.DVRIPClient") as client:
        camera = client.return_value.__enter__.return_value
        camera.read.side_effect = lambda name: order.append(name) or {"PTZ": True}
        result = probe(Config(password="test-only"))
    assert order == ["onvif", "onvif", "SystemInfo", "SystemFunction"]
    assert result["dvrip"]["state"] == "authenticated"
    assert result["active_protocol"] is None
    assert result["dvrip"]["control_enabled"] is False
