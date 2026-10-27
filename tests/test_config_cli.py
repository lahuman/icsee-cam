import json
import pytest
from icsee_ptz.config import Config, CameraError, AuthenticationError, redact, safe_error
from icsee_ptz.__main__ import main, duration


def test_password_missing_is_explicit(monkeypatch, capsys):
    monkeypatch.delenv("ICSEE_PASSWORD", raising=False)
    assert main(["status"]) == 2
    assert "ICSEE_PASSWORD" in capsys.readouterr().err


def test_password_not_in_repr():
    assert "private-value" not in repr(Config(password="private-value"))


@pytest.mark.parametrize("host", ["8.8.8.8", "example.com", "127.0.0.1", "::1", "169.254.1.1"])
def test_only_lan_camera_addresses(host):
    with pytest.raises(CameraError):
        Config(host=host)


@pytest.mark.parametrize("value", ["0", "-1", "2.1", "nan", "inf"])
def test_duration_limit(value):
    with pytest.raises(Exception):
        duration(value)


@pytest.mark.parametrize("command", ["move right --duration 0.5", "stop", "presets"])
def test_no_password_no_control(command, capsys, monkeypatch):
    monkeypatch.delenv("ICSEE_PASSWORD", raising=False)
    assert main(command.split()) == 2
    assert "ICSEE_PASSWORD" in capsys.readouterr().err


def test_redaction():
    value = redact({"password_present": True, "PassWord": "hash",
                    "SessionID": "token", "data": ["echo hidden-value"]}, "hidden-value")
    assert value["password_present"] is True
    assert "hidden-value" not in json.dumps(value)
    assert "hash" not in json.dumps(value)
    assert value["SessionID"] == "[REDACTED]"


def test_error_output_never_contains_remote_body():
    from zeep.exceptions import Fault
    from onvif.utils.exceptions import ONVIFOperationException
    error = ONVIFOperationException("GetServices", Fault("NotAuthorized secret-value"))
    assert safe_error(error) == {"kind": "authentication"}
    assert safe_error(AuthenticationError("ICSEE_PASSWORD missing"))["kind"] == "authentication"


def test_report_output_outside_project_refused(tmp_path, capsys):
    assert main(["probe", "--output", str(tmp_path / "result.json")]) == 2
    assert "프로젝트 내부" in capsys.readouterr().err


@pytest.mark.parametrize("enabled", [False, True])
def test_clock_schedule_status_has_no_camera_access(enabled, monkeypatch, capsys):
    from unittest.mock import Mock
    daily = Mock()
    daily.status.return_value = {"daily_at": "04:00", "next_run": "2026-10-27T04:00:00+09:00", "last_attempt": None}
    camera = Mock()
    monkeypatch.setattr("icsee_ptz.daily_clock.DailyClockSync", Mock(return_value=daily))
    monkeypatch.setattr("icsee_ptz.camera_clock.camera_clock", camera)
    monkeypatch.setenv("ICSEE_DAILY_CLOCK_SYNC", "1" if enabled else "0")
    assert main(["clock", "--schedule-status"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["enabled"] is enabled
    assert (state["next_run"] is not None) is enabled
    camera.assert_not_called()
