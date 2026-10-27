"""All media locks and durable queues are isolated from the running installation."""
import pytest
import json


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('ICSEE_RUNTIME_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('ICSEE_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('ICSEE_CONFIG_DIR', str(tmp_path / 'config'))
    # Public tests must not depend on the owner's physical camera profile.
    profile = tmp_path / 'camera-profile.json'
    profile.write_text(json.dumps({
        'host': '192.168.1.168', 'hardware': 'TEST_CAMERA', 'firmware': 'TEST_FIRMWARE',
        'protocol': 'dvrip', 'legacy_start': True,
        'physical_motion_and_stop': 'confirmed_by_user',
        'preset_protocol': 'onvif', 'preset_stop_protocol': 'dvrip',
        'preset_motion_and_stop': 'confirmed_by_user',
    }))
    monkeypatch.setenv('ICSEE_CAMERA_PROFILE', str(profile))
    monkeypatch.delenv('ICSEE_HOST', raising=False)
    monkeypatch.delenv('ICSEE_USERNAME', raising=False)
