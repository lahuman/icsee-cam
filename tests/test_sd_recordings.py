import struct
import threading
from datetime import datetime
from unittest.mock import Mock,patch
import pytest
from fastapi.testclient import TestClient
from icsee_ptz.config import Config,CameraError,ProtocolError
from icsee_ptz.sd_recordings import Demux,SDService,query
from icsee_ptz.web import create_app

RECORD={'name':'/idea0/2026-10-25/001/12.00.00-12.05.00[R].h264','start':'2026-10-25 12:00:00','end':'2026-10-25 12:05:00','event':'일반 녹화'}
HEADERS={'Origin':'http://127.0.0.1:8000','X-ICSEE-Control':'local'}


def test_demux_fragmented_video_audio_and_clip_limit():
    key=b'\0\0\1\xfc'+bytes([2,2,8,6])+struct.pack('<II',0,3)+b'key'
    audio=b'\0\0\1\xfa'+bytes([14,2])+struct.pack('<H',2)+b'ab'
    delta=b'\0\0\1\xfd'+struct.pack('<I',3)+b'del'
    demux=Demux(1)
    raw=key+audio+delta+delta
    for i in range(0,len(raw),3):demux.feed(raw[i:i+3])
    assert demux.video==b'keydel'
    assert demux.audio==b'ab'
    assert demux.done and demux.frames==2


def test_demux_rejects_unsupported_codec_and_huge_frames():
    with pytest.raises(ProtocolError):Demux(1).feed(b'\0\0\1\xfc'+bytes([3,12,8,6])+struct.pack('<II',0,1)+b'x')
    with pytest.raises(ProtocolError):Demux(1).feed(b'\0\0\1\xfd'+struct.pack('<I',9*1024**2))


def test_search_time_limit_is_checked_before_network():
    with patch('icsee_ptz.sd_recordings.DVRIPClient') as client:
        with pytest.raises(CameraError):query(Config(), '2026-10-20 00:00:00','2026-10-25 00:00:00')
    client.assert_not_called()


def test_cancelled_preview_never_connects(tmp_path):
    service=SDService(Config(),tmp_path)
    event=threading.Event();event.set()
    with patch('icsee_ptz.sd_recordings.download') as downloader:
        with pytest.raises(CameraError):service.prepare('0'*24,RECORD['start'],10,event)
    downloader.assert_not_called()


def test_first_keyframe_timestamp_is_preserved():
    t=datetime(2026,10,25,19,5,0)
    packed=(t.year-2000)<<26|t.month<<22|t.day<<17|t.hour<<12|t.minute<<6|t.second
    demux=Demux(1)
    demux.feed(b'\0\0\1\xfc'+bytes([2,12,8,6])+struct.pack('<II',packed,1)+b'x')
    assert demux.first_time==t


def test_prepare_only_queried_records_and_reuses_cache(tmp_path,monkeypatch):
    monkeypatch.setattr('icsee_ptz.sd_recordings.query',lambda *_:([RECORD],False))
    service=SDService(Config(),tmp_path/'cache')
    record=service.search(RECORD['start'],RECORD['end'])['recordings'][0]
    def fetch(config,record,start,seconds,folder,cancel):
        from pathlib import Path
        Path(folder,'preview.mp4').write_bytes(b'preview')
        return {'duration':10,'received_bytes':7,'audio':True}
    downloader=Mock(side_effect=fetch)
    monkeypatch.setattr('icsee_ptz.sd_recordings.download',downloader)
    event=threading.Event()
    with pytest.raises(CameraError):service.prepare('0'*24,RECORD['start'],10,event)
    with pytest.raises(CameraError):service.prepare(record['id'],'2026-10-25 13:00:00',10,event)
    first=service.prepare(record['id'],RECORD['start'],10,event)
    second=service.prepare(record['id'],RECORD['start'],10,event)
    assert first['cached'] is False and second['cached'] is True
    downloader.assert_called_once()


def test_sd_routes_are_explicit_local_requests_and_range_capable(tmp_path):
    service=Mock();service.root=tmp_path
    import threading, time
    service.guard=threading.Lock();service.records={"b"*24: (time.monotonic(), RECORD)}
    service.search.return_value={'recordings':[],'more':False}
    service.prepare.return_value={'id':'a'*64,'duration':1,'received_bytes':10,'audio':False}
    (tmp_path/('a'*64+'.mp4')).write_bytes(b'0123456789')
    manager=Mock();manager.status.return_value={}
    app=create_app(allow_unauthenticated=True, config=Config(password='test'),manager=manager,sd_service=service)
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',123)) as client:
        client.get('/sd.js');service.search.assert_not_called();service.prepare.assert_not_called()
        assert client.post('/api/sd/search',json={'start':RECORD['start'],'end':RECORD['end']}).status_code==403
        assert client.post('/api/sd/search',json={'start':RECORD['start'],'end':RECORD['end']},headers=HEADERS).status_code==200
        body={'recording_id':'b'*24,'start':RECORD['start'],'seconds':61}
        assert client.post('/api/sd/preview',json=body,headers=HEADERS).status_code==422
        body['seconds']=10
        assert client.post('/api/sd/preview',json=body,headers=HEADERS).status_code==200
        response=client.get('/api/sd/preview/'+'a'*64+'.mp4',headers={'Range':'bytes=1-3'})
        assert response.status_code==206 and response.content==b'123'
        assert client.get('/api/sd/preview/invalid.mp4').status_code==404
    manager.check.assert_not_called()
