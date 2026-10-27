"""Non-secret record of a user-observed motor test; never used as credentials."""
import json
from pathlib import Path


def profile_path():
    import os
    from .paths import config_root, project_root
    if os.environ.get("ICSEE_CAMERA_PROFILE"):
        return Path(os.environ["ICSEE_CAMERA_PROFILE"])
    if (config_root() / "camera.json").exists():
        return config_root() / "camera-profile.json"
    return project_root() / "camera-profile.json"


def read_profile():
    try:
        profile = json.loads(profile_path().read_text())
        return profile if isinstance(profile, dict) else None
    except (OSError, ValueError):
        return None


def verified_profile(config):
    profile = read_profile()
    if (profile and profile.get("host") == config.host and profile.get("protocol") == "dvrip"
            and all(isinstance(profile.get(key), str) and profile[key] for key in ("hardware", "firmware"))
            and type(profile.get("legacy_start", True)) is bool
            and profile.get("physical_motion_and_stop") == "confirmed_by_user"):
        mapping = profile.get("direction_map")
        directions = {"left", "right", "up", "down"}
        if mapping is not None and (not isinstance(mapping, dict) or set(mapping) != directions
                                   or any(not isinstance(value, str) for value in mapping.values())
                                   or set(mapping.values()) != directions):
            return None
        return profile
    return None
