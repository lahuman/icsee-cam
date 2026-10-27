"""Exercise the shipping JS with controlled timing; no physical motor movement."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_ptz_input_safety_and_stream_recovery():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('frontend_safety.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_audio_click_toggle_and_microphone_cleanup():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('audio_frontend.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_tracking_confirmation_and_uncertain_write_recovery():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('tracking_frontend.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_timelapse_regeneration_and_korean_capture_time():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('timelapse_frontend.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_role_visibility_and_admin_operating_status():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('permissions_frontend.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_preset_admin_ui_uncertain_results_and_stop():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    result = subprocess.run([node, str(Path(__file__).with_name('presets_frontend.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
