"""Restart only the web service while receiving video; verify readiness afterward."""
import json
import subprocess
import time
import requests
from .docker_web_check import main as check_web


def main():
    session = requests.Session()
    session.trust_env = False
    with session.get('http://127.0.0.1:8000/video', stream=True, timeout=(5, 8)) as response:
        response.raise_for_status()
        assert next(response.iter_content(8192))
        started = time.monotonic()
        result = subprocess.run(['docker', 'compose', 'restart', 'web'],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Docker web restart failed')
    check_web()
    print(json.dumps({'restart_and_readiness_seconds': round(time.monotonic()-started, 2),
                      'active_video_before_restart': True, 'recovered': True}))


if __name__ == '__main__':
    main()
