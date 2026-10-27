from unittest.mock import MagicMock, Mock

from fastapi.testclient import TestClient
import pytest

from icsee_ptz.config import Config, ProtocolError
from icsee_ptz.verification import verified_profile
from icsee_ptz.web import create_app

HEADERS = {"X-ICSEE-Control": "local", "Origin": "http://127.0.0.1:8000"}


def setup():
    config = Config(password="test-camera-secret")
    verified = verified_profile(config)
    camera = MagicMock()
    camera.__enter__.return_value = camera
    camera.read.return_value = {"HardWare": verified["hardware"], "SoftWareVersion": verified["firmware"]}
    camera.tracking.return_value = {"Enable": 0}
    camera.set_tracking.return_value = {"enabled": True}
    factory = Mock(return_value=camera)
    app = create_app(allow_unauthenticated=True, config=config, manager=Mock(), tracking_factory=factory)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 12345))
    return client, camera, factory


def test_tracking_load_only_reads_and_explicit_toggle_writes():
    client, camera, factory = setup()
    with client:
        assert client.get("/").status_code == 200
        assert client.get("/tracking.js").status_code == 200
        factory.assert_not_called()
        assert client.get("/api/settings/tracking").json() == {"enabled": False}
        camera.set_tracking.assert_not_called()
        response = client.post("/api/settings/tracking", json={"enabled": True}, headers=HEADERS)
        assert response.status_code == 200 and response.json() == {"enabled": True}
        assert response.headers["cache-control"] == "no-store"
        camera.set_tracking.assert_called_once_with(True)
    assert camera.__exit__.call_count == 2


@pytest.mark.parametrize("body", [{}, {"enabled": "false"}, {"enabled": 1},
                                 {"enabled": None}, {"enabled": True, "Sensitivity": 5}])
def test_tracking_rejects_ambiguous_or_extra_inputs(body):
    client, camera, factory = setup()
    with client:
        assert client.post("/api/settings/tracking", json=body, headers=HEADERS).status_code == 422
    factory.assert_not_called()


def test_tracking_obeys_existing_origin_host_and_control_header_guards():
    client, camera, factory = setup()
    with client:
        for headers in ({}, {**HEADERS, "Origin": "https://evil.example"},
                        {**HEADERS, "Host": "evil.example"}):
            assert client.post("/api/settings/tracking", json={"enabled": True}, headers=headers).status_code == 403
    factory.assert_not_called()


def test_tracking_wrong_camera_or_failed_verification_never_reports_success():
    client, camera, factory = setup()
    with client:
        camera.read.return_value = {"HardWare": "other-camera"}
        assert client.post("/api/settings/tracking", json={"enabled": True}, headers=HEADERS).status_code == 503
        camera.set_tracking.assert_not_called()
        camera.read.return_value = {"HardWare": verified_profile(Config())["hardware"],
                                    "SoftWareVersion": verified_profile(Config())["firmware"]}
        camera.set_tracking.side_effect = ProtocolError("자동 추적 상태를 확인하지 못했습니다.")
        response = client.post("/api/settings/tracking", json={"enabled": True}, headers=HEADERS)
        assert response.status_code == 503
        assert "test-camera-secret" not in response.text
        assert "확인하지 못했습니다" in response.json()["error"]["message"]
