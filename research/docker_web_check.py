"""Read-only API and access checks against the running Compose web service."""
import json
import time
from pathlib import Path
import requests


def main():
    base = "http://127.0.0.1:8000"
    session = requests.Session()
    session.trust_env = False
    deadline = time.monotonic() + 45
    while True:
        try:
            status = session.get(base + "/api/status", timeout=3)
            status.raise_for_status()
            state = status.json()
            if state.get("connected") is True:
                break
        except requests.RequestException:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Docker web readiness deadline exceeded")
        time.sleep(1)
    checks = {
        "status_http": status.status_code,
        "camera_connected": state.get("connected"),
        "movement_active": state.get("active"),
        "wrong_host": session.get(base, headers={"Host": "external.example"}, timeout=5).status_code,
        "wrong_origin": session.post(base + "/api/move", json={"direction": "left"},
            headers={"Origin": "https://external.example", "X-ICSEE-Control": "local"}, timeout=5).status_code,
        "invalid_direction": session.post(base + "/api/move", json={"direction": "invalid"},
            headers={"Origin": base, "X-ICSEE-Control": "local"}, timeout=5).status_code,
    }
    Path("research/docker-web-check.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))
    assert checks == {"status_http": 200, "camera_connected": True, "movement_active": False,
                      "wrong_host": 403, "wrong_origin": 403, "invalid_direction": 422}


if __name__ == "__main__":
    main()
