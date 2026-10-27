"""Narrow, authenticated web control of the camera's built-in motion tracking."""
from pathlib import Path
import threading

from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from .config import ProtocolError, safe_error
from .dvrip import DVRIPClient


class TrackingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def attach_tracking(app, config, verified, factory=None, operation_lock=None):
    factory = factory or (lambda: DVRIPClient(config))
    lock = threading.Lock()

    def operate(enabled=None):
        if not verified:
            return JSONResponse({"error": {"message": "자동 추적 설정은 장비의 이동·정지 검증 후 사용할 수 있습니다."}}, status_code=409)
        # Keep each read/write/read transaction together across browser clients.
        if not lock.acquire(blocking=False):
            return JSONResponse({"error": {"message": "자동 추적 설정을 처리 중입니다. 잠시 후 상태를 새로고침해 주세요."}}, status_code=409)
        control_acquired = False
        try:
            if enabled is not None and operation_lock is not None:
                control_acquired = operation_lock.acquire(blocking=False)
                if not control_acquired:
                    return JSONResponse({"error": {"message": "다른 카메라 제어를 처리 중입니다."}}, status_code=409)
            with factory() as client:
                info = client.read("SystemInfo")
                if (info.get("HardWare") != verified["hardware"]
                        or info.get("SoftWareVersion") != verified["firmware"]):
                    raise ProtocolError("검증 기록과 실제 카메라 또는 펌웨어가 다릅니다. 설정 변경을 중단합니다.")
                if enabled is None:
                    return {"enabled": bool(client.tracking()["Enable"])}
                return client.set_tracking(enabled)
        except Exception as exc:
            return JSONResponse({"error": safe_error(exc)}, status_code=503)
        finally:
            if control_acquired:
                operation_lock.release()
            lock.release()

    @app.get("/api/settings/tracking")
    def get_tracking():
        return operate()

    @app.post("/api/settings/tracking")
    def set_tracking(body: TrackingBody):
        return operate(body.enabled)

    @app.get("/tracking.js")
    def tracking_script():
        return Response(Path(__file__).with_name("static").joinpath("tracking.js").read_text(),
                        media_type="text/javascript")
