"""Explicit, bounded ONVIF motor operations; discovery never calls these."""
from datetime import timedelta
import math
import threading
import time

from .config import CameraError, ProtocolError, safe_error
from .onvif_client import ONVIFProbe, PTZ, PTZ_NS

DIRECTIONS = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}


class ONVIFControl:
    def __init__(self, config, port=8899):
        config.require_password()
        self.probe = ONVIFProbe(config, port)
        try:
            self.evidence = self.probe.probe()
            profiles = [p for p in self.evidence.get("profiles", [])
                        if p.get("continuous_pan_tilt_advertised")]
            if not self.evidence.get("endpoint_confirmed") or not profiles:
                raise ProtocolError("ONVIF ContinuousMove 지원 profile을 확인하지 못했습니다.")
            self.profile = profiles[0]
            endpoint = self.evidence["resolved_services"][PTZ_NS]
            self.ptz = self.probe.service(PTZ, endpoint)
            # Separate HTTP session so a slow move response cannot block watchdog STOP.
            self.watchdog_ptz = self.probe.service(PTZ, endpoint)
        except BaseException:
            self.probe.close()
            raise

    def close(self):
        self.probe.close()

    @property
    def profile_token(self):
        return self.profile["profile_token"]

    def stop(self):
        self.ptz.Stop(ProfileToken=self.profile_token, PanTilt=True, Zoom=True)
        return {"protocol": "onvif", "stop_response": "accepted"}

    def status(self):
        from zeep.helpers import serialize_object
        return serialize_object(self.ptz.GetStatus(ProfileToken=self.profile_token))

    def presets(self):
        presets = self.ptz.GetPresets(ProfileToken=self.profile_token)
        return [{"name": p.Name, "preset_token": p.token} for p in presets or []]

    def move(self, direction, duration=0.5, speed=0.2):
        if direction not in DIRECTIONS:
            raise CameraError("알 수 없는 이동 방향입니다.")
        if not math.isfinite(duration) or not 0 < duration <= 2:
            raise CameraError("이동 시간은 0초 초과, 최대 2초입니다.")
        if not math.isfinite(speed) or not 0 < speed <= 1:
            raise CameraError("속도는 0 초과 1 이하여야 합니다.")
        spaces = self.profile["options"]["Spaces"]["ContinuousPanTiltVelocitySpace"]
        x, y = (axis * speed for axis in DIRECTIONS[direction])
        space = next((s for s in spaces if s["XRange"]["Min"] <= x <= s["XRange"]["Max"]
                      and s["YRange"]["Min"] <= y <= s["YRange"]["Max"]), None)
        if space is None:
            raise CameraError("요청 속도가 장비의 PTZ velocity 범위를 벗어났습니다.")
        limits = self.profile["options"]["PTZTimeout"]
        minimum = limits["Min"].total_seconds()
        maximum = limits["Max"].total_seconds()
        camera_timeout = max(duration, minimum)
        if camera_timeout > min(maximum, 2):
            raise CameraError("장비 측 자동 정지 시간을 2초 이내로 제한할 수 없습니다.")
        stops, stop_errors = [], []

        def stop_with(service, source):
            try:
                service.Stop(ProfileToken=self.profile_token, PanTilt=True, Zoom=True)
                stops.append(source)
            except Exception as exc:
                stop_errors.append({"source": source, **safe_error(exc)})

        # Verify STOP works before the first move; this cannot start the motor.
        self.stop()
        timer = threading.Timer(duration, stop_with, (self.watchdog_ptz, "watchdog"))
        timer.daemon = True
        started = time.monotonic()
        move_error = None
        interrupted = False
        try:
            timer.start()
            self.ptz.ContinuousMove(
                ProfileToken=self.profile_token,
                Velocity={"PanTilt": {"x": x, "y": y, "space": space["URI"]}},
                Timeout=timedelta(seconds=camera_timeout))
            remaining = duration - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            move_error = safe_error(exc)
        finally:
            stop_with(self.ptz, "finally")
            timer.cancel()
            timer.join()  # Wait for an already-started watchdog request before closing sessions.
        result = {"protocol": "onvif", "direction": direction, "duration": duration,
                  "speed": speed, "camera_timeout": camera_timeout,
                  "move_response": "error" if move_error or interrupted else "accepted",
                  "stop_acknowledged_by": stops, "stop_errors": stop_errors,
                  "physical_motion_verified": False}
        if move_error:
            result["move_error"] = move_error
        if interrupted:
            result["interrupted"] = True
        return result
