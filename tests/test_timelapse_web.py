from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient

from icsee_ptz.config import Config
from icsee_ptz import timelapse as tl
from icsee_ptz.web import create_app


@pytest.fixture
def archive_client(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive=tl.Archive(tmp_path/'archive', '192.168.1.168')
    manager=Mock(); manager.status.return_value={}
    app=create_app(allow_unauthenticated=True, config=Config(password='hidden'), manager=manager, archive=archive)
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1',123)) as client:
        yield archive,client


def add_export(archive):
    archive.save(b'fixture', datetime(2026,10,24,0,tzinfo=timezone.utc))
    path=archive.root/'2026-10-24/timelapse-30fps.mp4'
    path.write_bytes(b'0123456789')
    with archive.db() as db:
        db.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?,?)',
                   ('2026-10-24',30,'2026-10-24/timelapse-30fps.mp4',1,'hash',10,.5,'now'))
    return path


def test_date_list_and_range_playback(archive_client):
    archive,client=archive_client
    add_export(archive)
    data=client.get('/api/timelapse/days').json()
    assert data['timezone']=='Asia/Seoul'
    assert data['days'][0]['date']=='2026-10-24'
    assert data['days'][0]['videos'][0]['stale'] is False
    assert 'root' not in data and 'hidden' not in str(data)
    response=client.get('/api/timelapse/2026-10-24/video?fps=30',headers={'Range':'bytes=2-5'})
    assert response.status_code==206
    assert response.content==b'2345'
    assert response.headers['content-type']=='video/mp4'
    assert response.headers['content-range']=='bytes 2-5/10'
    assert 'media-src' in client.get('/').headers['content-security-policy']


def test_empty_dates_and_missing_or_invalid_media(archive_client):
    archive,client=archive_client
    assert client.get('/api/timelapse/days').json()['days']==[]
    assert client.get('/api/timelapse/2026-10-24/video?fps=30').status_code==404
    assert client.get('/api/timelapse/2026-99-99/video?fps=30').status_code==400
    assert client.get('/api/timelapse/2026-10-24/video?fps=99').status_code==422
    assert client.get('/api/timelapse/not-a-date/video').status_code==400


def test_unregistered_or_outside_symlink_is_not_served(archive_client,tmp_path):
    archive,client=archive_client
    path=add_export(archive)
    path.unlink()
    outside=tmp_path/'outside.mp4';outside.write_bytes(b'private')
    path.symlink_to(outside)
    assert client.get('/api/timelapse/2026-10-24/video?fps=30').status_code==404
    path.unlink();path.write_bytes(b'unregistered')
    with archive.db() as db: db.execute('DELETE FROM exports')
    assert client.get('/api/timelapse/2026-10-24/video?fps=30').status_code==404


def test_stale_video_and_explicit_protected_render(archive_client,monkeypatch):
    archive,client=archive_client
    add_export(archive)
    archive.save(b'next',datetime(2026,10,24,1,tzinfo=timezone.utc))
    assert client.get('/api/timelapse/days').json()['days'][0]['videos'][0]['stale']
    render=Mock(return_value={'frame_count':2,'duration':1,'source_hash':'new-hash'})
    monkeypatch.setattr(archive,'render',render)
    url='/api/timelapse/2026-10-24/render'
    assert client.post(url,json={'fps':30}).status_code==403
    headers={'Origin':'http://127.0.0.1:8000','X-ICSEE-Control':'local'}
    response=client.post(url,json={'fps':30},headers=headers)
    assert response.status_code==202
    import time
    for _ in range(100):
        job = client.get(response.json()['status_url']).json()
        if job['state'] in ('succeeded', 'failed'):
            break
        time.sleep(.02)
    assert job['state']=='succeeded'
    assert job['result']['url']=='/api/timelapse/2026-10-24/video?fps=30&v=new-hash'
    assert render.call_args.args == ('2026-10-24',30)
    assert 'cancel' in render.call_args.kwargs
    assert client.post(url,json={'fps':999},headers=headers).status_code==422
    assert client.post('/api/timelapse/2026-10-23/render',json={'fps':30},headers=headers).status_code==404


def test_regenerated_video_has_new_playback_and_download_url(archive_client):
    archive,client=archive_client
    path=add_export(archive)
    old=client.get('/api/timelapse/days').json()['days'][0]['videos'][0]
    assert old['verified_at']=='now'
    assert client.get(old['url']).content==b'0123456789'
    # Publish a different export at the same filesystem path, as render() does.
    path.write_bytes(b'new-video-contents')
    with archive.db() as db:
        db.execute("UPDATE exports SET source_hash=?,bytes=?,verified_at=?",('new-hash',18,'later'))
    new=client.get('/api/timelapse/days').json()['days'][0]['videos'][0]
    assert new['url']!=old['url']
    assert new['verified_at']=='later'
    response=client.get(new['url'],headers={'Range':'bytes=0-8'})
    assert response.status_code==206
    assert response.content==b'new-video'
    assert response.headers['cache-control']=='no-store'


def test_video_only_day_is_listed_and_slow_mode_is_rejected(archive_client):
    archive,client=archive_client
    path=add_export(archive)
    with archive.db() as db:db.execute('DELETE FROM frames')
    data=client.get('/api/timelapse/days').json()['days'][0]
    assert data['date']=='2026-10-24' and not data['photos_retained']
    assert data['videos'][0]['stale'] is False
    assert client.get('/api/timelapse/2026-10-24/video').status_code==200
    assert client.get('/api/timelapse/2026-10-24/video?fps=2').status_code==422
    headers={'Origin':'http://127.0.0.1:8000','X-ICSEE-Control':'local'}
    assert client.post('/api/timelapse/2026-10-24/render',json={'fps':2},headers=headers).status_code==422
    assert 'archive-fps' not in client.get('/').text
