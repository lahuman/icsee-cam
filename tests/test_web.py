import threading,time
import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient
from icsee_ptz.config import Config
from icsee_ptz.web import create_app,MovementManager

HEADERS={'X-ICSEE-Control':'local','Origin':'http://127.0.0.1:8000'}
def client(manager):
    return TestClient(create_app(allow_unauthenticated=True, config=Config(password='never-show-this'), manager=manager,
        video_factory=lambda:iter([b'--frame\r\nContent-Type: image/jpeg\r\n\r\nbytes\r\n'])),
        base_url='http://127.0.0.1:8000', client=('127.0.0.1',12345))

def test_loading_page_only_checks_connection_and_never_moves():
    m=Mock();m.status.return_value={'connected':True,'active':False}
    with client(m) as c:
        r=c.get('/')
        assert r.status_code==200 and '방향 제어' in r.text
        assert 'never-show-this' not in r.text
        assert c.get('/api/status').json()['protocol']=='dvrip'
        assert c.get('/video').status_code==200
        assert c.get('/docs').status_code==404
        css = c.get('/camera.css')
        assert css.status_code == 200
        assert css.headers['content-type'].startswith('text/css')
        assert 'never-show-this' not in css.text
    m.start.assert_not_called()
    m.check.assert_not_called()


def test_container_bridge_client_requires_explicit_mode_and_local_host():
    for mode, expected in ((False, 403), (True, 200)):
        manager = Mock(); manager.status.return_value = {}
        with TestClient(create_app(allow_unauthenticated=True, config=Config(password='test'), manager=manager, container_mode=mode),
                        base_url='http://127.0.0.1:8000', client=('172.20.0.1', 123)) as c:
            assert c.get('/').status_code == expected
            assert c.get('/', headers={'host': 'camera.example'}).status_code == 403
            assert c.post('/api/stop', headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403


def test_video_disconnect_closes_source_generator():
    import asyncio
    closed=[]
    def stream():
        try:
            while True:
                yield b'video'
        finally:
            closed.append(True)
    manager=Mock()
    app=create_app(allow_unauthenticated=True, config=Config(password='test'),manager=manager,video_factory=stream)
    response=next(route for route in app.routes if route.path=='/video').endpoint()
    async def exercise():
        delivered=asyncio.Event()
        async def send(message):
            if message['type']=='http.response.body':
                delivered.set()
        async def receive():
            await delivered.wait()
            return {'type':'http.disconnect'}
        await asyncio.wait_for(response({'type':'http','method':'GET',
            'asgi':{'spec_version':'2.0'}},receive,send),3)
    asyncio.run(exercise())
    assert closed==[True]


def test_snapshot_fallback_is_memory_only_and_closes_camera(monkeypatch):
    camera=Mock(); camera.capture.return_value=b'\xff\xd8test\xff\xd9'
    monkeypatch.setattr('icsee_ptz.web.SnapshotClient', lambda _:camera)
    manager=Mock(); manager.status.return_value={}
    with client(manager) as c:
        response=c.get('/snapshot')
        assert response.status_code==200
        assert response.headers['content-type']=='image/jpeg'
        assert response.headers['cache-control']=='no-store'
        assert response.content==camera.capture.return_value
    camera.close.assert_called_once()

def test_control_api_enforces_origin_host_and_valid_inputs():
    m=Mock();m.status.return_value={}
    with client(m) as c:
        assert c.post('/api/move',json={'direction':'right'}).status_code==403
        assert c.post('/api/move',json={'direction':'right'},headers={**HEADERS,'Origin':'https://evil.example'}).status_code==403
        assert c.get('/',headers={'Host':'evil.example'}).status_code==403
        assert c.post('/api/move',json={'direction':'home'},headers=HEADERS).status_code==422
        assert c.post('/api/move',json={'direction':'up','speed':0},headers=HEADERS).status_code==422
        assert c.post('/api/move',json={'direction':'up','speed':.25},headers=HEADERS).status_code==202
        m.start.assert_called_once_with('up',.25)
        assert c.post('/api/stop',headers=HEADERS).status_code==202
        m.stop.assert_called_once()

def test_release_during_connect_prevents_late_movement():
    ready=threading.Event(); controller=Mock()
    def factory():ready.wait(1);return controller
    m=MovementManager(factory);m.start('left',.25);m.stop();ready.set();m.worker.join(2)
    controller.move.assert_not_called()
    controller.close.assert_called_once()

def test_release_interrupts_running_move():
    moving=threading.Event();controller=Mock()
    def move(direction,duration,speed,*,cancel_event):
        moving.set();cancel_event.wait(1)
        return {'move_response':'accepted','stop_acknowledged_by':['finally']}
    controller.move.side_effect=move
    m=MovementManager(lambda:controller);m.start('up',.25)
    assert moving.wait(1);m.stop();m.worker.join(2)
    assert m.result['stop_acknowledged_by']==['finally']

def test_disconnect_or_missing_release_has_two_second_deadline():
    controller=Mock()
    def move(direction,duration,speed,*,cancel_event):
        assert 0<duration<=2
        assert cancel_event.wait(2.5)
        return {'move_response':'accepted','stop_acknowledged_by':['watchdog']}
    controller.move.side_effect=move
    m=MovementManager(lambda:controller);m.start('right',.25);m.worker.join(3)
    assert not m.worker.is_alive()
    assert m.result['stop_acknowledged_by']==['watchdog']


def test_stop_without_active_worker_still_sends_real_stop():
    controller=Mock();m=MovementManager(lambda:controller)
    m.stop()
    controller.stop.assert_called_once()
    controller.close.assert_called_once()


def test_raw_cancellation_serializes_video_close_with_inflight_read():
    import asyncio
    reading = threading.Event()
    finish_read = threading.Event()
    closed = threading.Event()
    def source():
        try:
            yield b'first'
            reading.set()
            finish_read.wait(2)
            yield b'second'
        finally:
            closed.set()
    app = create_app(allow_unauthenticated=True, config=Config(password='test'), manager=Mock(), video_factory=source)
    response = next(route for route in app.routes if route.path == '/video').endpoint()
    async def exercise():
        iterator = response.body_iterator
        assert await anext(iterator) == b'first'
        pending = asyncio.create_task(anext(iterator))
        assert await asyncio.to_thread(reading.wait, 1)
        pending.cancel()  # Raw task cancellation, rather than an AnyIO scope.
        await asyncio.sleep(.02)
        assert not closed.is_set(), 'source must remain open until its read finishes'
        finish_read.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()
    asyncio.run(exercise())
