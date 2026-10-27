import asyncio
import re
import threading
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import CameraError, safe_error
from .sd_recordings import SDService
from .paths import data_root

class SearchBody(BaseModel):
    start:str=Field(max_length=19)
    end:str=Field(max_length=19)

class PreviewBody(BaseModel):
    recording_id:str=Field(pattern=r'^[a-f0-9]{24}$')
    start:str=Field(max_length=19)
    seconds:int=Field(default=30,ge=1,le=60)


def attach_sd(app,config,service=None):
    service=service or SDService(config,data_root()/'sd-preview')
    @app.get('/sd.js')
    def script():
        return FileResponse(Path(__file__).with_name('static')/'sd.js',media_type='text/javascript')
    @app.post('/api/sd/search')
    def search(body:SearchBody):
        try:return service.search(body.start,body.end)
        except CameraError as exc:raise HTTPException(409,str(exc)) from None
        except Exception as exc:raise HTTPException(503,safe_error(exc).get('kind','connection')) from None
    @app.post('/api/sd/preview')
    async def preview(body:PreviewBody,request:Request):
        cancel=threading.Event()
        jobs = app.state.jobs
        with service.guard:
            record = service.records.get(body.recording_id)
        import time
        if not record or time.monotonic() - record[0] > 900:
            raise HTTPException(409, '녹화 목록을 다시 조회해 주세요.')
        principal = getattr(request.state, 'principal', {'username': 'test'})
        try:
            job = jobs.submit('sd', {'recording_id': body.recording_id, 'record': record[1],
                                    'start': body.start, 'seconds': body.seconds,
                                    'expires_at': time.time() + max(0, 900 - (time.monotonic() - record[0]))}, principal['username'])
        except CameraError as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': '5'}) from None
        async def wait_job():
            while True:
                state = await asyncio.to_thread(jobs.get, job['id'])
                if state['state'] == 'succeeded':
                    return state['result']
                if state['state'] in ('failed', 'cancelled'):
                    raise CameraError('SD 미리보기 작업이 실패하거나 취소됐습니다.')
                await asyncio.sleep(.2)
        task=asyncio.create_task(wait_job())
        try:
            while not task.done():
                if await request.is_disconnected():
                    cancel.set()
                    raise HTTPException(499,'영상 가져오기가 취소됐습니다.')
                await asyncio.sleep(.2)
            result=task.result()
            return {**result,'url':f"/api/sd/preview/{result['id']}.mp4"}
        except CameraError as exc:raise HTTPException(409,str(exc)) from None
        except HTTPException:raise
        except Exception as exc:raise HTTPException(503,safe_error(exc).get('kind','connection')) from None
        finally:
            cancel.set()
            if not task.done():
                jobs.cancel(job['id'])
                task.cancel()
            # Retrieve exceptions after a disconnected request without keeping an
            # abandoned download alive beyond its next bounded socket read.
            if not task.done():
                task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
    @app.get('/api/sd/preview/{token}.mp4')
    def video(token:str):
        if not re.fullmatch(r'[a-f0-9]{64}',token):raise HTTPException(404,'영상이 없습니다.')
        path=service.root/(token+'.mp4')
        if path.is_symlink() or not path.is_file() or service.root.resolve() not in path.resolve().parents:
            raise HTTPException(404,'임시 영상이 없습니다. 다시 가져와 주세요.')
        return FileResponse(path,media_type='video/mp4',filename='camera-recording.mp4',content_disposition_type='inline')
    return service
