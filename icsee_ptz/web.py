import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import threading
import time
import anyio
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from .config import CameraError, redact, safe_error
from .dvrip_control import DVRIPControl
from .verification import verified_profile
from .video import mjpeg
from .snapshot import SnapshotClient
from .reconnect import Backoff, retryable


class MoveBody(BaseModel):
    direction: Literal["left", "right", "up", "down"]
    speed: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)


class ReadOnlyConnection:
    """Check authentication without opening a PTZ controller."""
    def __init__(self, config):
        from .dvrip import DVRIPClient
        with DVRIPClient(config) as client:
            client.read("SystemInfo")

    def close(self):
        pass


class MovementManager:
    def __init__(self, factory):
        self.factory = factory
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.worker = None
        self.cancel = threading.Event()
        self.result = None
        self.connected = None
        self.last_check = None
        self.retry_blocked = False
        self.check_lock = threading.Lock()

    def check(self):
        if not self.check_lock.acquire(blocking=False):
            return
        try:
            controller = self.factory()
            controller.close()
            self.connected = True
            self.retry_blocked = False
            if self.result and "error" in self.result:
                self.result = None
        except Exception as exc:
            self.connected = False
            self.retry_blocked = not retryable(exc)
            self.result = {"error": safe_error(exc)}
        finally:
            self.last_check = time.time()
            self.check_lock.release()

    async def monitor(self):
        backoff = Backoff()
        while True:
            if not self.retry_blocked and not (self.worker and self.worker.is_alive()):
                await asyncio.to_thread(self.check)
            if self.connected:
                backoff.reset()
                delay = 30
            else:
                delay = backoff.next()
            await asyncio.sleep(delay)

    def start(self, direction, speed):
        with self.lock:
            if self.connected is False:
                raise CameraError("카메라 연결 대기 중입니다. 연결 복구 후 다시 눌러 주세요.")
            if self.worker and self.worker.is_alive():
                raise CameraError("이전 이동의 STOP 처리를 기다려 주세요.")
            if not self.operation_lock.acquire(blocking=False):
                raise CameraError("다른 카메라 제어를 처리 중입니다.")
            self.cancel = threading.Event()
            cancel = self.cancel
            deadline = time.monotonic() + 2
            expiry = threading.Timer(2, cancel.set)
            expiry.daemon = True
            self.result = {"state": "connecting", "direction": direction}

            def run():
                controller = None
                try:
                    controller = self.factory()
                    self.connected = True
                    remaining = deadline - time.monotonic()
                    if cancel.is_set() or remaining <= 0:
                        self.result = {"state": "cancelled_before_move"}
                        return
                    self.result = {"state": "moving", "direction": direction}
                    self.result = controller.move(direction, remaining, speed, cancel_event=cancel)
                except Exception as exc:
                    self.connected = False
                    self.retry_blocked = not retryable(exc)
                    self.result = {"error": safe_error(exc)}
                finally:
                    expiry.cancel()
                    try:
                        if controller:
                            controller.close()
                    finally:
                        self.operation_lock.release()
            self.worker = threading.Thread(target=run, daemon=True)
            try:
                expiry.start()
                self.worker.start()
            except BaseException:
                expiry.cancel()
                self.operation_lock.release()
                raise

    def stop(self):
        self.cancel.set()  # Interrupts the worker's wait and runs its finally STOP.
        if not self.worker or not self.worker.is_alive():
            controller = None
            try:
                controller = self.factory()
                self.result = controller.stop()
            except Exception as exc:
                self.result = {"error": safe_error(exc)}
            finally:
                if controller:
                    controller.close()

    def status(self):
        with self.lock:
            return {"connected": self.connected, "last_connection_check": self.last_check,
                    "retry_blocked": self.retry_blocked,
                    "active": bool(self.worker and self.worker.is_alive()), "last_result": self.result}

    def close(self):
        self.cancel.set()
        if self.worker:
            self.worker.join(timeout=12)


def create_app(config, *, manager=None, video_factory=None, port=8000,
               talk_factory=None, listen_factory=None, container_mode=False, archive=None, sd_service=None,
               lan_ip=None, web_auth=None, public_origin=None, tracking_factory=None, clock_scheduler=None, allow_unauthenticated=False, preset_service=None):
    if web_auth is None and not allow_unauthenticated:
        raise CameraError("웹 인증 설정이 필요합니다.")
    config.require_password()
    verified = verified_profile(config)
    manager = manager or MovementManager(
        (lambda: DVRIPControl(config, legacy_start=verified.get("legacy_start", True), verified=verified))
        if verified else (lambda: ReadOnlyConnection(config)))
    video_factory = video_factory or (lambda: mjpeg(config))
    from .media_runtime import SharedStream, sync_stream, ViewerRegistry, ViewerMiddleware, ManagedStreamingResponse
    shared_video = SharedStream(lambda: sync_stream(video_factory))
    viewers = ViewerRegistry()

    @asynccontextmanager
    async def lifespan(app):
        instance_lock = None
        if not allow_unauthenticated:
            import fcntl
            from .paths import runtime_root
            root = runtime_root()
            root.mkdir(parents=True, exist_ok=True)
            instance_lock = (root / 'web-instance.lock').open('a')
            try:
                fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                instance_lock.close()
                raise CameraError('같은 데이터 디렉터리의 웹 서버는 하나만 실행할 수 있습니다.') from None
        jobs.start()
        if clock_scheduler:
            clock_scheduler.start()
        try:
            yield
        finally:
            if clock_scheduler:
                await asyncio.to_thread(clock_scheduler.close)
            await asyncio.to_thread(jobs.close)
            await audio.shutdown()
            await shared_video.close()
            await asyncio.to_thread(manager.close)
            if instance_lock:
                instance_lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
    operation_lock = manager.operation_lock if isinstance(manager, MovementManager) else threading.Lock()
    app.state.operation_lock = operation_lock
    from .web_access import WebAccess
    access=WebAccess(port,container_mode,lan_ip,public_origin)
    app.add_middleware(ViewerMiddleware, registry=viewers)
    app.state.viewers = viewers
    if web_auth is not None:
        from .web_auth import attach_auth
        attach_auth(app, web_auth, secure_hosts=access.secure_hosts)
    from .audio_web import attach_audio
    audio = attach_audio(app, config, port, talk_factory=talk_factory, listen_factory=listen_factory,
                         container_mode=container_mode,access=access)
    app.state.audio = audio
    from .timelapse import Archive, archive_root
    from .timelapse_web import attach_timelapse
    from .sd_web import attach_sd
    from .jobs import JobQueue, executor
    from .jobs_web import attach_jobs
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def get_archive():
        return archive or Archive(archive_root(), config.host)

    app.state.sd = attach_sd(app, config, sd_service)
    jobs = JobQueue(executor=executor(config, get_archive, app.state.sd), host=config.host)
    app.state.jobs = jobs
    attach_jobs(app, jobs, get_archive)
    attach_timelapse(app, get_archive, jobs)
    from .tracking_web import attach_tracking
    attach_tracking(app, config, verified, tracking_factory, operation_lock=operation_lock)
    from .presets_web import attach_presets
    presets = attach_presets(app, config, verified, operation_lock, preset_service)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in access.hosts:
            return JSONResponse({"error": "local host required"}, status_code=403)
        if not request.client or not access.client_allowed(request.client.host):
            return JSONResponse({"error": "loopback client required"}, status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if (origin and not access.origin_allowed(host,origin)) or request.headers.get("x-icsee-control") != "local":
                return JSONResponse({"error": "same-origin control required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self'; media-src 'self'; connect-src 'self' {access.websocket_sources()}; frame-ancestors 'none'")
        return response

    @app.exception_handler(Exception)
    async def error_handler(request, exc):
        return JSONResponse({"error": safe_error(exc)}, status_code=503)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("static").joinpath("index.html").read_text()

    @app.get('/live.js')
    def live_script():
        return Response(Path(__file__).with_name('static').joinpath('live.js').read_text(),
                        media_type='text/javascript')

    @app.get('/camera.css')
    def camera_styles():
        return Response(Path(__file__).with_name('static').joinpath('camera.css').read_text(),
                        media_type='text/css')

    @app.get('/permissions.js')
    def permissions_script():
        return Response(Path(__file__).with_name('static').joinpath('permissions.js').read_text(), media_type='text/javascript')

    @app.get('/session.js')
    def session_script():
        return Response(Path(__file__).with_name('static').joinpath('session.js').read_text(),
                        media_type='text/javascript')

    @app.get("/video")
    def video():
        return ManagedStreamingResponse(shared_video.subscribe(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot")
    def snapshot():
        client = SnapshotClient(config)
        try:
            return Response(client.capture(), media_type="image/jpeg")
        finally:
            client.close()

    @app.get("/api/status")
    def status():
        return redact({"host": config.host, "protocol": "dvrip", "max_duration": 2,
                       **manager.status(), "control_enabled": bool(verified),
                       "setup_required": not bool(verified),
                       "setup_message": "" if verified else "영상 확인 모드입니다. 서버에서 register 후 verify-motion으로 이동·정지를 확인하세요.",
                       "direction_map": verified.get("direction_map", {"up": "down", "down": "up", "left": "right", "right": "left"}) if verified else {},
                       }, config.password)

    def require_verified():
        if not verified:
            raise HTTPException(status_code=409, detail="방향 제어는 register → verify-motion 완료 후 서버를 다시 시작하면 활성화됩니다.")

    @app.post("/api/move", status_code=202)
    def move(body: MoveBody):
        require_verified()
        try:
            manager.start(body.direction, body.speed)
        except CameraError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"state": "accepted", "max_duration": 2}

    @app.post("/api/stop", status_code=202)
    def stop():
        require_verified()
        if not presets.stop():
            manager.stop()
        return {"state": "stop_requested"}

    @app.post("/api/check")
    def check():
        manager.check()
        return status()

    return app
