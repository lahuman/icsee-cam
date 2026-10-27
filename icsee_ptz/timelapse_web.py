"""Date-based local archive browsing; exposes only verified archive MP4 paths."""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import CameraError
from .timelapse import KST


class RenderBody(BaseModel):
    fps: Literal[30] = 30


def attach_timelapse(app, archive_factory, jobs=None):
    def video_url(day, fps, export):
        # A new export must also be a new media resource to the browser player.
        return f'/api/timelapse/{day}/video?' + urlencode({'fps': fps, 'v': export['source_hash']})

    def day_value(day):
        try:
            if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, '날짜는 YYYY-MM-DD 형식이어야 합니다.') from None
        return day

    def media_path(archive, day, fps):
        path = archive.root / day / f'timelapse-{fps}fps.mp4'
        resolved = path.resolve()
        if archive.root not in resolved.parents or path.is_symlink() or not resolved.is_file():
            return None
        # Never serve an arbitrary file merely because its name resembles an export.
        with archive.db() as db:
            row = db.execute('SELECT path FROM exports WHERE day=? AND fps=?', (day, fps)).fetchone()
        if not row or row['path'] != str(path.relative_to(archive.root)):
            return None
        return resolved

    @app.get('/timelapse.js')
    def script():
        return FileResponse(Path(__file__).with_name('static') / 'timelapse.js', media_type='text/javascript')

    @app.get('/api/timelapse/days')
    def days(limit: int = Query(default=100, ge=1, le=366), offset: int = Query(default=0, ge=0)):
        archive = archive_factory()
        status = archive.status()
        today = datetime.now(KST).date()
        result = []
        selected = list(reversed(status['days']))[offset:offset + limit]
        export_index = {(e['day'], e['fps']): e for e in status['exports']}
        for item in selected:
            variants = []
            for fps in (30,):
                export = export_index.get((item['day'], fps))
                if export and media_path(archive, item['day'], fps):
                    variants.append({'fps': fps, 'duration': export['duration'], 'bytes': export['bytes'],
                                     'frames': export['frame_count'], 'stale': item['photos_retained'] and export['frame_count'] != item['frames'],
                                     'verified_at': export['verified_at'],
                                     'url': video_url(item['day'], fps, export)})
            result.append({'date': item['day'], 'frames': item['frames'], 'jpeg_bytes': item['jpeg_bytes'],
                           'last_capture': item['last_capture'], 'photos_retained': item['photos_retained'], 'videos': variants})
        return {'timezone': 'Asia/Seoul', 'today': today.isoformat(),
                'yesterday': (today - timedelta(days=1)).isoformat(),
                'recording': status['running'], 'days': result, 'total': len(status['days']),
                'next_offset': offset + limit if offset + limit < len(status['days']) else None}

    @app.get('/api/timelapse/{day}/video')
    def video(day: str, fps: int = 30):
        if fps != 30:
            raise HTTPException(422, '하루 요약은 30fps만 지원합니다.')
        day_value(day)
        path = media_path(archive_factory(), day, fps)
        if not path:
            raise HTTPException(404, '선택한 날짜의 영상이 없습니다. 영상 만들기를 실행하세요.')
        return FileResponse(path, media_type='video/mp4', filename=f'{day}-{fps}fps.mp4',
                            content_disposition_type='inline')

    @app.post('/api/timelapse/{day}/render')
    def render(day: str, body: RenderBody, request: Request):
        day_value(day)
        archive = archive_factory()
        if day not in archive.days():
            raise HTTPException(404, '선택한 날짜에 저장된 사진이 없습니다.')
        if jobs is not None:
            principal = getattr(request.state, 'principal', {'username': 'test'})
            try:
                job = jobs.submit('render', {'root': str(archive.root), 'day': day, 'fps': body.fps}, principal['username'])
            except CameraError as exc:
                raise HTTPException(429, str(exc), headers={'Retry-After': '5'}) from None
            return JSONResponse({'job_id': job['id'], 'state': job['state'],
                                 'status_url': '/api/jobs/' + job['id']}, status_code=202)
        try:
            result = archive.render(day, body.fps)
        except CameraError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'date': day, 'fps': body.fps, 'frames': result['frame_count'],
                'duration': result['duration'], 'url': video_url(day, body.fps, result)}
