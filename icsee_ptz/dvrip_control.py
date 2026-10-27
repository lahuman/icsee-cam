import math
import threading
import time

from .config import CameraError, safe_error
from .dvrip import DVRIPClient


class DVRIPControl:
    """Two authenticated connections; independent watchdog sends the STOP packet."""
    def __init__(self, config, *, legacy_start=False, verified=None):
        self.legacy_start = legacy_start
        self.client = DVRIPClient(config)
        self.watchdog = DVRIPClient(config)
        try:
            self.client.__enter__()
            self.device_information = self.client.read("SystemInfo")
            if verified and (self.device_information.get("HardWare") != verified["hardware"]
                    or self.device_information.get("SoftWareVersion") != verified["firmware"]):
                raise CameraError("검증 기록과 실제 장비/펌웨어가 다릅니다. 자동 제어를 거부합니다.")
            self.watchdog.__enter__()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.client.close()
        self.watchdog.close()

    def stop(self):
        self.client.ptz("up", stop=True)
        return {"protocol": "dvrip", "stop_response": "accepted", "ret": 100}

    def move(self, direction, duration=0.5, speed=0.25, *, max_duration=2, legacy_start=None,
             cancel_event=None):
        if legacy_start is None:
            legacy_start = getattr(self, "legacy_start", False)
        if direction not in ("left", "right", "up", "down"):
            raise CameraError("알 수 없는 이동 방향입니다.")
        if max_duration not in (2, 3) or not math.isfinite(duration) or not 0 < duration <= max_duration:
            raise CameraError("기본 이동 제한은 2초이며 별도 지정된 시험은 최대 3초입니다.")
        if not math.isfinite(speed) or not 0 < speed <= 1:
            raise CameraError("속도는 0 초과 1 이하여야 합니다.")
        # Normalize to the device's eight integer steps; default 0.2 -> step 1.
        step = max(1, min(8, int(speed * 8)))
        self.stop()
        self.watchdog.ptz("up", stop=True)
        if cancel_event is not None and cancel_event.is_set():
            return {"protocol": "dvrip", "move_response": "cancelled",
                    "stop_acknowledged_by": ["preflight"], "stop_errors": []}
        stops, errors = [], []

        def stop(client, source):
            try:
                client.ptz("up", stop=True)
                stops.append(source)
            except Exception as exc:
                errors.append({"source": source, **safe_error(exc)})

        timer = threading.Timer(duration, stop, (self.watchdog, "watchdog"))
        timer.daemon = True
        started = time.monotonic()
        failure = None
        interrupted = False
        try:
            timer.start()
            options = {"legacy_start": True} if legacy_start else {}
            self.client.ptz(direction, step=step, **options)
            remaining = duration - (time.monotonic() - started)
            if remaining > 0:
                if cancel_event is None:
                    time.sleep(remaining)
                else:
                    cancel_event.wait(remaining)
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            failure = safe_error(exc)
        finally:
            stop(self.client, "finally")
            timer.cancel()
            timer.join()
            # An early release can cancel the timer before it fires. If the
            # primary STOP failed, still attempt STOP on the independent link.
            if not stops:
                stop(self.watchdog, "watchdog_retry")
        result = {"protocol": "dvrip", "direction": direction, "duration": duration,
                  "speed": speed, "step": step,
                  "start_preset": 0 if legacy_start else 65535,
                  "move_response": "error" if failure or interrupted else "accepted",
                  "stop_acknowledged_by": stops, "stop_errors": errors,
                  "physical_motion_verified": False, "camera_timeout_supported": False}
        if failure:
            result["move_error"] = failure
        if interrupted:
            result["interrupted"] = True
        return result
