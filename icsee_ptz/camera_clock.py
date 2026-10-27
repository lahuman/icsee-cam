"""Explicit, one-shot camera clock synchronization to server time in Korea."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import ProtocolError
from .dvrip import DVRIPClient
from .verification import verified_profile

KST = ZoneInfo("Asia/Seoul")


def camera_clock(config, *, sync=False, factory=None):
    verified = verified_profile(config)
    if not verified:
        raise ProtocolError("검증 기록이 없는 카메라의 시간 설정은 지원하지 않습니다.")
    with (factory or (lambda: DVRIPClient(config)))() as client:
        info = client.read("SystemInfo")
        if (info.get("HardWare") != verified["hardware"]
                or info.get("SoftWareVersion") != verified["firmware"]):
            raise ProtocolError("검증 기록과 실제 카메라 또는 펌웨어가 다릅니다.")
        before = client.camera_time().replace(tzinfo=KST)
        observed = datetime.now(KST)
        result = {"timezone": "Asia/Seoul", "camera_before": before.isoformat(),
                  "server_time": observed.isoformat(),
                  "offset_before_seconds": round((before-observed).total_seconds(), 3),
                  "synchronized": False}
        if sync:
            client.set_camera_time(datetime.now(KST))
            actual = client.camera_time().replace(tzinfo=KST)
            now = datetime.now(KST)
            offset = (actual-now).total_seconds()
            if abs(offset) > 2:
                raise ProtocolError("시간 변경 후 카메라와 서버의 오차가 2초를 초과합니다. 현재 시간을 다시 확인해 주세요.")
            result.update(synchronized=True, camera_after=actual.isoformat(),
                          server_time=now.isoformat(), offset_after_seconds=round(offset, 3))
        return result
