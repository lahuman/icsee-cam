import asyncio
from pathlib import Path
import threading
import anyio
from fastapi import Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .audit import event
from .config import CameraError, ProtocolError, safe_error
from .presets import PresetService, PresetBusy


class SavePreset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=64)
    token: str | None = Field(default=None, min_length=1, max_length=128)


class PresetToken(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    token: str = Field(min_length=1, max_length=128)


def attach_presets(app, config, verified, operation_lock, service=None):
    service = service or PresetService(config, verified, operation_lock)
    app.state.presets = service

    def perform(kind, call):
        try:
            result = call()
            if kind != 'list':
                event('preset_operation', kind=kind, state=result['state'], role='admin')
            return result
        except Exception as exc:
            status = 409 if isinstance(exc, PresetBusy) else 503
            if isinstance(exc, CameraError) and not isinstance(exc, ProtocolError):
                status = 409
                error = {'message': str(exc)}
            else:
                error = safe_error(exc)
            if kind != 'list':
                error['message'] = error.get('message', '카메라 처리 결과를 확인하지 못했습니다.') + ' 변경 요청은 자동 재시도하지 않습니다. 목록과 영상을 확인해 주세요.'
            event('preset_operation', kind=kind, status=status, state='unconfirmed')
            return JSONResponse({'error': error}, status_code=status)

    @app.get('/presets.js')
    def script():
        return Response(Path(__file__).with_name('static').joinpath('presets.js').read_text(), media_type='text/javascript')

    @app.get('/api/admin/presets')
    def listing():
        return perform('list', service.list)

    @app.post('/api/admin/presets/save')
    def save(body: SavePreset):
        return perform('save', lambda: service.save(body.name, body.token))

    @app.post('/api/admin/presets/remove')
    def remove(body: PresetToken):
        return perform('remove', lambda: service.remove(body.token))

    @app.post('/api/admin/presets/goto')
    async def move(body: PresetToken, request: Request):
        cancel = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(perform, 'goto', lambda: service.move(body.token, cancel)))
        try:
            while not task.done():
                if await request.is_disconnected():
                    cancel.set()
                await asyncio.sleep(.05)
            return await task
        finally:
            cancel.set()
            with anyio.CancelScope(shield=True):
                await asyncio.shield(task)

    @app.post('/api/admin/presets/stop')
    def stop():
        return {'state': 'stop_requested' if service.stop() else 'idle'}

    return service
