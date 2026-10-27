from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from .config import CameraError


class RetentionBody(BaseModel):
    photo_days: int = Field(ge=1, le=365, strict=True)
    video_days: int = Field(ge=1, le=365, strict=True)


def attach_jobs(app, jobs, archive_factory=None):
    def permitted(request, token):
        job = jobs.get(token)
        principal = getattr(request.state, 'principal', {'username': 'test', 'role': 'admin'})
        if not job or (job['owner'] != principal['username'] and principal['role'] != 'admin'):
            raise HTTPException(404, '작업이 없습니다.')
        return job

    @app.get('/api/jobs/{token}')
    def status(token: str, request: Request):
        job = permitted(request, token)
        return {key: value for key, value in job.items() if key != 'owner'}

    @app.post('/api/jobs/{token}/cancel')
    def cancel(token: str, request: Request):
        permitted(request, token)
        jobs.cancel(token)
        return {'state': 'cancel_requested'}

    @app.get('/api/admin/runtime')
    def runtime():
        archive = archive_factory().status() if archive_factory else {}
        return {'storage_bytes': archive.get('storage_bytes'), 'disk_free_bytes': archive.get('disk_free_bytes'),
                'last_run': archive.get('last_run'), 'recent_errors': archive.get('recent_errors', []),
                'viewers': app.state.viewers.count(), 'max_viewers': 3,
                'jobs': jobs.summary(), 'max_encoders': 3, 'batch_workers': 2}

    @app.get('/api/admin/retention')
    def retention():
        return archive_factory().retention_policy()

    @app.post('/api/admin/retention')
    def set_retention(body: RetentionBody):
        try:
            return archive_factory().set_retention_policy(body.photo_days, body.video_days)
        except CameraError as exc:
            raise HTTPException(422, str(exc)) from None
