import asyncio
import threading
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from icsee_ptz.config import Config
from icsee_ptz.web import create_app

ORIGIN={'origin':'http://127.0.0.1:8000','host':'127.0.0.1:8000'}
def create(talk=None,listen=None):
    manager=Mock();manager.status.return_value={}
    return TestClient(create_app(allow_unauthenticated=True, config=Config(password='private-password'),manager=manager,
        talk_factory=talk,listen_factory=listen),base_url='http://127.0.0.1:8000',
        client=('127.0.0.1',12345))


def test_page_and_scripts_never_open_microphone_or_camera_audio():
    factory=Mock()
    with create(talk=factory,listen=factory) as c:
        assert '영상 음성 제어' in c.get('/').text
        assert c.get('/audio-worklet.js').status_code==200
        script=c.get('/audio.js').text
        assert 'getUserMedia' in script and "getTracks().forEach(track=>track.stop())" in script
        assert 'private-password' not in script
    factory.assert_not_called()


@pytest.mark.parametrize('origin',[None,'https://external.example','http://localhost:8000'])
def test_websocket_rejects_wrong_or_missing_origin(origin):
    factory=Mock()
    with create(talk=factory) as c:
        with pytest.raises(WebSocketDisconnect) as error:
            with c.websocket_connect('/audio/talk',headers={'host':'127.0.0.1:8000'} if origin is None else {'host':'127.0.0.1:8000','origin':origin}):pass
        assert error.value.code==1008
    factory.assert_not_called()


def test_talk_pcm_is_forwarded_then_release_closes_session():
    talk=Mock();talk.open.return_value={'state':'ready'}
    with create(talk=lambda:talk) as c:
        with c.websocket_connect('/audio/talk',headers=ORIGIN) as ws:
            assert ws.receive_json()['state']=='ready'
            ws.send_bytes(b'\0'*640);ws.send_text('stop')
            with pytest.raises(WebSocketDisconnect):ws.receive_bytes()
    talk.send_pcm.assert_called_once_with(b'\0'*640)
    talk.close.assert_called_once()


def test_talk_rejects_oversized_packet_and_closes():
    talk=Mock();talk.open.return_value={'state':'ready'}
    with create(talk=lambda:talk) as c:
        with c.websocket_connect('/audio/talk',headers=ORIGIN) as ws:
            ws.receive_json();ws.send_bytes(b'x'*2000)
            with pytest.raises(WebSocketDisconnect) as error:ws.receive_bytes()
            assert error.value.code==1009
    talk.send_pcm.assert_not_called();talk.close.assert_called_once()


def test_browser_disconnect_always_closes_talk():
    talk=Mock();talk.open.return_value={'state':'ready'}
    with create(talk=lambda:talk) as c:
        with c.websocket_connect('/audio/talk',headers=ORIGIN) as ws:ws.receive_json()
    talk.close.assert_called_once()


def test_missing_samples_ends_talk_session(monkeypatch):
    import icsee_ptz.audio_web as module
    monkeypatch.setattr(module,'MAX_TALK_SECONDS',.05)
    talk=Mock();talk.open.return_value={'state':'ready'}
    with create(talk=lambda:talk) as c:
        with c.websocket_connect('/audio/talk',headers=ORIGIN) as ws:
            ws.receive_json()
            with pytest.raises(WebSocketDisconnect):ws.receive_bytes()
    talk.close.assert_called_once()


def test_only_one_talk_session_at_a_time():
    talk=Mock();talk.open.return_value={'state':'ready'}
    with create(talk=lambda:talk) as c:
        with c.websocket_connect('/audio/talk',headers=ORIGIN) as first:
            first.receive_json()
            with pytest.raises(WebSocketDisconnect) as error:
                with c.websocket_connect('/audio/talk',headers=ORIGIN):pass
            assert error.value.code==1013
            first.send_text('stop')


def test_listen_delivers_memory_only_pcm_and_closes_generator():
    closed=[]
    async def stream():
        try:yield b'\0'*3200
        finally:closed.append(True)
    with create(listen=stream) as c:
        with c.websocket_connect('/audio/listen',headers=ORIGIN) as ws:
            assert ws.receive_json()['recording'] is False
            assert len(ws.receive_bytes())==3200
    assert closed==[True]


def test_listen_disconnect_cancels_stalled_source_and_allows_reconnect():
    closed = threading.Event()
    async def stream():
        try:
            yield b'\0' * 3200
            await asyncio.Event().wait()  # Camera stops delivering samples.
        finally:
            closed.set()
    with create(listen=stream) as c:
        with c.websocket_connect('/audio/listen', headers=ORIGIN) as first:
            first.receive_json()
            assert len(first.receive_bytes()) == 3200
            first.send({'type': 'websocket.disconnect', 'code': 1000})
            assert closed.wait(1), 'source must close without another PCM frame'
            with pytest.raises(WebSocketDisconnect):
                first.receive_bytes()  # Wait for cleanup and slot release.
        with c.websocket_connect('/audio/listen', headers=ORIGIN) as second:
            assert second.receive_json()['state'] == 'ready'
            assert len(second.receive_bytes()) == 3200
            second.send({'type': 'websocket.disconnect', 'code': 1000})
            with pytest.raises(WebSocketDisconnect):
                second.receive_bytes()


def test_container_audio_keeps_origin_guard_for_bridge_clients():
    async def stream():
        yield b'\0' * 3200
    manager = Mock(); manager.status.return_value = {}
    with TestClient(create_app(allow_unauthenticated=True, config=Config(password='test'), manager=manager,
                    listen_factory=stream, container_mode=True),
                    base_url='http://127.0.0.1:8000', client=('172.20.0.1', 123)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/audio/listen', headers={**ORIGIN, 'origin':'https://evil.example'}):
                pass
        with c.websocket_connect('/audio/listen', headers=ORIGIN) as ws:
            assert ws.receive_json()['state'] == 'ready'
            assert len(ws.receive_bytes()) == 3200


def test_listen_reconnects_after_camera_stream_loss(monkeypatch):
    from icsee_ptz.reconnect import Backoff
    monkeypatch.setattr(Backoff, 'next', lambda _: 0)
    attempts=[]
    async def stream():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError()
        yield b'\0' * 3200
    with create(listen=stream) as c:
        with c.websocket_connect('/audio/listen', headers=ORIGIN) as ws:
            assert ws.receive_json()['state'] == 'ready'
            assert ws.receive_json()['state'] == 'waiting'
            assert len(ws.receive_bytes()) == 3200
    assert len(attempts) == 2
