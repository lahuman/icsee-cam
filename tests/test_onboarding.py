import json
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient

from icsee_ptz import onboarding
from icsee_ptz.__main__ import main
from icsee_ptz.accounts import check_password, read_accounts
from icsee_ptz.config import CameraError, Config
from icsee_ptz.verification import profile_path, read_profile, verified_profile
from icsee_ptz.web import create_app


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ICSEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("ICSEE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ICSEE_CAMERA_PROFILE")
    # Remove the synthetic legacy fixture to simulate a clean installation.
    (tmp_path / "camera-profile.json").unlink()
    monkeypatch.delenv("ICSEE_PASSWORD", raising=False)
    monkeypatch.setattr("socket.create_connection", Mock(side_effect=AssertionError("No real camera access")))
    return tmp_path


def answers(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(values))


def setup(fresh, monkeypatch):
    answers(monkeypatch, ["web-admin"])
    monkeypatch.setattr("getpass.getpass", lambda _: "web-only-password")
    return onboarding.setup_installation("192.168.2.50", "camera-user")


def identity():
    return {"host": "192.168.2.50", "protocol": "dvrip", "hardware": "test-model", "firmware": "test-v1"}


def enroll(fresh, monkeypatch):
    setup(fresh, monkeypatch)
    monkeypatch.setenv("ICSEE_PASSWORD", "camera-secret")
    monkeypatch.setattr(onboarding, "device_identity", lambda _: identity())
    config = Config.from_env()
    onboarding.register_device(config)
    return config


def test_setup_and_repeat_preserve_credentials_without_network(fresh, monkeypatch):
    result = setup(fresh, monkeypatch)
    assert result["camera_password_saved"] is False
    users = read_accounts(fresh / "config/web-auth.json")
    assert check_password("web-only-password", users["web-admin"]["password_hash"])
    original = {p: p.read_bytes() for p in (fresh / "config").glob("*.json")}
    for path, data in original.items():
        assert b"web-only-password" not in data
        assert path.stat().st_mode & 0o777 == 0o600
    answers(monkeypatch, [])  # idempotent setup must not ask for passwords again
    assert onboarding.setup_installation()["state"] == "configured"
    assert {p: p.read_bytes() for p in original} == original
    with pytest.raises(CameraError, match="이미 설정"):
        onboarding.setup_installation("192.168.2.51")


def test_setup_mismatched_password_creates_nothing(fresh, monkeypatch):
    answers(monkeypatch, ["admin"])
    secrets = iter(["first-password", "other-password"])
    monkeypatch.setattr("getpass.getpass", lambda _: next(secrets))
    with pytest.raises(CameraError, match="일치"):
        onboarding.setup_installation("192.168.2.50", "admin")
    assert not (fresh / "config").exists()


def test_setup_refuses_to_shadow_legacy_profile(fresh, monkeypatch):
    legacy = fresh / "camera-profile.json"
    legacy.write_text(json.dumps(identity()))
    with pytest.raises(CameraError, match="기존 운영"):
        onboarding.setup_installation("192.168.2.50", "admin")
    assert json.loads(legacy.read_text()) == identity()


def test_config_precedence_and_invalid_config_fail_closed(fresh, monkeypatch):
    setup(fresh, monkeypatch)
    assert Config.from_env().host == "192.168.2.50"
    assert Config.from_env().username == "camera-user"
    monkeypatch.setenv("ICSEE_HOST", "192.168.2.51")
    monkeypatch.setenv("ICSEE_USERNAME", "override")
    assert Config.from_env().host == "192.168.2.51"
    assert Config.from_env("192.168.2.52", 5).host == "192.168.2.52"
    assert Config.from_env().username == "override"
    assert Config.from_env(timeout=5).timeout == 5
    (fresh / "config/camera.json").write_text('{"version": 1, "password": "must-not-appear"}')
    with pytest.raises(CameraError) as error:
        Config.from_env()
    assert "must-not-appear" not in str(error.value)


def test_doctor_is_local_and_explains_missing_steps(fresh, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert main(["doctor"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["network_checked"] and not report["ready"]
    setup(fresh, monkeypatch)
    monkeypatch.setenv("ICSEE_PASSWORD", "camera-secret")
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "camera-secret" not in output
    assert not json.loads(output)["ptz_verified"]


def test_registration_only_reads_identity_and_discards_raw_secrets(fresh, monkeypatch):
    setup(fresh, monkeypatch)
    monkeypatch.setenv("ICSEE_PASSWORD", "camera-secret")
    camera = MagicMock()
    camera.__enter__.return_value = camera
    camera.read.return_value = {"HardWare": "test-model", "SoftWareVersion": "test-v1",
                              "SerialNo": "private-serial", "SessionID": "private-token"}
    monkeypatch.setattr("icsee_ptz.dvrip.DVRIPClient", Mock(return_value=camera))
    config = Config.from_env()
    assert onboarding.register_device(config)["ptz_verified"] is False
    camera.read.assert_called_once_with("SystemInfo")
    camera.ptz.assert_not_called()
    assert verified_profile(config) is None
    saved = profile_path().read_text()
    assert all(secret not in saved for secret in ("private-serial", "private-token", "camera-secret"))
    assert onboarding.register_device(config)["state"] == "already_registered"
    camera.read.return_value["SoftWareVersion"] = "new-version"
    with pytest.raises(CameraError, match="기존 프로필"):
        onboarding.register_device(config)
    assert profile_path().read_text() == saved


def test_unverified_web_serves_video_but_blocks_control(fresh, monkeypatch):
    config = enroll(fresh, monkeypatch)
    manager = Mock()
    manager.status.return_value = {"connected": True, "active": False}
    tracking = Mock()
    app = create_app(config, manager=manager, tracking_factory=tracking,
                     video_factory=lambda: iter([b"frame"]), allow_unauthenticated=True)
    headers = {"Origin": "http://127.0.0.1:8000", "X-ICSEE-Control": "local"}
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 123)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/video").content == b"frame"
        for url in ("/api/status", "/api/check"):
            response = client.get(url) if url.endswith("status") else client.post(url, headers=headers)
            assert response.json()["control_enabled"] is False
        assert client.post("/api/move", json={"direction": "left"}, headers=headers).status_code == 409
        assert client.post("/api/stop", headers=headers).status_code == 409
        assert client.post("/api/settings/tracking", json={"enabled": True}, headers=headers).status_code == 409
    manager.start.assert_not_called()
    manager.stop.assert_not_called()
    tracking.assert_not_called()


def test_unverified_connection_check_never_constructs_ptz(fresh, monkeypatch):
    config = enroll(fresh, monkeypatch)
    camera = MagicMock()
    camera.__enter__.return_value = camera
    factory = Mock(return_value=camera)
    ptz = Mock(side_effect=AssertionError("PTZ must not be created"))
    monkeypatch.setattr("icsee_ptz.dvrip.DVRIPClient", factory)
    monkeypatch.setattr("icsee_ptz.web.DVRIPControl", ptz)
    app = create_app(config, allow_unauthenticated=True)
    app.state.manager.check()
    assert app.state.manager.connected is True
    camera.read.assert_called_once_with("SystemInfo")
    camera.ptz.assert_not_called()
    ptz.assert_not_called()


@pytest.mark.parametrize("mode", ["legacy", "modern"])
def test_verify_records_observed_mapping_and_mode(fresh, monkeypatch, mode):
    config = enroll(fresh, monkeypatch)
    controller = Mock()
    controller.move.return_value = {"move_response": "accepted", "stop_acknowledged_by": ["finally"], "stop_errors": []}
    factory = Mock(return_value=controller)
    monkeypatch.setattr("icsee_ptz.dvrip_control.DVRIPControl", factory)
    answers(monkeypatch, [value for direction in ("right", "left", "down", "up") for value in ("MOVE", direction, "STOP")])
    assert onboarding.verify_motion(config, mode)["ptz_verified"] is True
    profile = verified_profile(config)
    assert profile["direction_map"]["right"] == "left"
    assert profile["legacy_start"] is (mode == "legacy")
    assert controller.move.call_count == 4 and controller.close.call_count == 4
    assert all(call.args[1:] == (0.5, 0.25) for call in controller.move.call_args_list)
    assert all(call.kwargs["legacy_start"] is (mode == "legacy") for call in factory.call_args_list)
    assert "preset_motion_and_stop" not in profile


@pytest.mark.parametrize("inputs,result", [
    ([""], None),
    (["MOVE"], {"move_response": "accepted", "stop_acknowledged_by": [], "stop_errors": []}),
    (["MOVE", "", ""], {"move_response": "accepted", "stop_acknowledged_by": ["finally"]}),
    (["MOVE", "left", ""], {"move_response": "accepted", "stop_acknowledged_by": ["finally"]}),
])
def test_verify_cancellation_or_failed_stop_preserves_profile(fresh, monkeypatch, inputs, result):
    config = enroll(fresh, monkeypatch)
    before = read_profile()
    controller = Mock()
    controller.move.return_value = result
    factory = Mock(return_value=controller)
    monkeypatch.setattr("icsee_ptz.dvrip_control.DVRIPControl", factory)
    answers(monkeypatch, inputs)
    with pytest.raises(CameraError):
        onboarding.verify_motion(config, "legacy")
    assert read_profile() == before and verified_profile(config) is None
    if result is None:
        factory.assert_not_called()
    else:
        controller.close.assert_called_once()


def test_verify_refuses_running_web_server(fresh, monkeypatch):
    import fcntl
    from icsee_ptz.paths import runtime_root
    config = enroll(fresh, monkeypatch)
    runtime_root().mkdir(parents=True)
    with (runtime_root() / "web-instance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(CameraError, match="웹 서버"):
            onboarding.verify_motion(config, "legacy")


@pytest.mark.parametrize("value", [[], {"host": "192.168.2.50", "protocol": "dvrip", "physical_motion_and_stop": "confirmed_by_user"}])
def test_malformed_profile_never_enables_control(fresh, value):
    profile_path().write_text(json.dumps(value))
    assert verified_profile(Config(host="192.168.2.50")) is None
