import asyncio
import json
import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from icsee_ptz.accounts import password_record, read_accounts, write_accounts
from icsee_ptz.config import Config, CameraError
from icsee_ptz.security import ClientIdentity, RequestLimits
from icsee_ptz.web import create_app
from icsee_ptz.web_auth import WebAuth, COOKIE
from icsee_ptz.media_runtime import media_slot, MediaBusy, SharedStream

ORIGIN = 'http://127.0.0.1:8000'
HEADERS = {'origin': ORIGIN, 'x-icsee-control': 'local'}


def connection(peer, forwarded):
    return HTTPConnection({'type': 'http', 'client': (peer, 123), 'headers': [(b'x-forwarded-for', forwarded.encode())]})


def test_forwarded_identity_only_trusts_explicit_proxy_chain():
    identity = ClientIdentity('10.0.0.2/32')
    assert identity.identify(connection('192.168.1.3', '1.2.3.4')) == '192.168.1.3'
    assert identity.identify(connection('10.0.0.2', '1.2.3.4, 192.168.1.3')) == '192.168.1.3'
    assert identity.identify(connection('10.0.0.2', '192.168.1.3')) == '192.168.1.3'
    with pytest.raises(CameraError):
        identity.identify(connection('10.0.0.2', 'not-an-ip'))
    with pytest.raises(CameraError):
        ClientIdentity('0.0.0.0/0')


def app(auth, stream=None):
    manager = Mock(); manager.status.return_value = {}
    return create_app(Config(password='dummy-camera-only'), manager=manager, sd_service=Mock(),
                      listen_factory=stream, web_auth=auth, container_mode=True)


def login(client, name='owner'):
    return client.post('/api/login', json={'username': name, 'password': 'dummy-password-only'}, headers=HEADERS)


def test_one_clients_bad_requests_do_not_lock_out_another():
    auth = WebAuth('owner', 'dummy-password-only')
    application = app(auth)
    with TestClient(application, base_url=ORIGIN, client=('192.168.1.10', 1)) as first:
        for _ in range(20):
            assert first.post('/api/login', json={}, headers=HEADERS).status_code == 400
        assert login(first).status_code == 429
    with TestClient(application, base_url=ORIGIN, client=('192.168.1.11', 1)) as second:
        assert login(second).status_code == 200


@pytest.mark.parametrize('action', ['logout', 'expiry'])
def test_existing_audio_revoked_without_new_request(action):
    closed = threading.Event()
    async def stream():
        try:
            yield b'first'
            await asyncio.Event().wait()
        finally:
            closed.set()
    auth = WebAuth('owner', 'dummy-password-only')
    with TestClient(app(auth, stream), base_url=ORIGIN) as client:
        assert login(client).status_code == 200
        with client.websocket_connect('ws://127.0.0.1:8000/audio/listen', headers=HEADERS) as ws:
            ws.receive_json(); assert ws.receive_bytes() == b'first'
            if action == 'logout':
                assert client.post('/api/logout', headers=HEADERS).status_code == 200
            else:
                auth.sessions[client.cookies.get(COOKIE)] = 0
            assert closed.wait(2)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_bytes()
        assert not auth.connections


def test_default_factory_requires_authentication():
    with pytest.raises(CameraError, match='인증'):
        create_app(Config(password='dummy'))


def test_viewer_cannot_control_or_enqueue_but_can_read_and_check():
    auth = WebAuth('owner', 'dummy-password-only')
    auth.users['reader'] = {'username': 'reader', 'role': 'viewer', 'enabled': True,
                            'password_hash': password_record('dummy-password-only')}
    application = app(auth)
    with TestClient(application, base_url=ORIGIN) as client:
        assert login(client, 'reader').status_code == 200
        assert client.get('/api/me').json()['role'] == 'viewer'
        for path in ['/api/move', '/api/stop', '/api/settings/tracking', '/api/timelapse/2026-01-01/render', '/api/sd/preview']:
            assert client.post(path, json={}, headers=HEADERS).status_code == 403
        assert client.get('/api/status').status_code == 200
        assert client.post('/api/check', headers=HEADERS).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('ws://127.0.0.1:8000/audio/talk', headers=HEADERS):
                pass
    application.state.manager.start.assert_not_called()


def test_stop_remains_available_when_control_budget_is_exhausted():
    auth = WebAuth('owner', 'dummy-password-only')
    auth.limits.limits['control'] = 1
    with TestClient(app(auth), base_url=ORIGIN) as client:
        login(client)
        assert client.post('/api/check', headers=HEADERS).status_code == 200
        assert client.post('/api/check', headers=HEADERS).status_code == 429
        assert client.post('/api/stop', headers=HEADERS).status_code == 202


def test_file_account_change_revokes_existing_session(tmp_path):
    path = tmp_path / 'accounts.json'
    source = WebAuth('owner', 'dummy-password-only')
    source.users['reader'] = {'username': 'reader', 'role': 'operator', 'enabled': True,
                              'password_hash': password_record('dummy-password-only')}
    write_accounts(path, source.users)
    auth = WebAuth.from_file(path)
    token = auth.issue('reader')
    users = read_accounts(path)
    users['reader']['role'] = 'viewer'
    write_accounts(path, users)
    auth.next_reload = 0
    auth.refresh()
    assert not auth.valid_token(token)
    assert 'dummy-password-only' not in path.read_text()


def test_three_viewers_share_single_audio_source_and_fourth_is_rejected():
    calls = []
    async def stream():
        calls.append(1)
        while True:
            await asyncio.sleep(.01)
            yield b'audio'
    auth = WebAuth('owner', 'dummy-password-only')
    tokens = [auth.issue() for _ in range(4)]
    application = app(auth, stream)
    with TestClient(application, base_url=ORIGIN) as client:
        from contextlib import ExitStack
        with ExitStack() as stack:
            for token in tokens[:3]:
                ws = stack.enter_context(client.websocket_connect('ws://127.0.0.1:8000/audio/listen',
                    headers={**HEADERS, 'cookie': COOKIE + '=' + token}))
                ws.receive_json(); assert ws.receive_bytes() == b'audio'
            assert application.state.viewers.count() == 3
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect('ws://127.0.0.1:8000/audio/listen',
                        headers={**HEADERS, 'cookie': COOKIE + '=' + tokens[3]}):
                    pass
            assert error.value.code == 1013
            assert len(calls) == 1
    assert application.state.viewers.count() == 0


def test_cross_process_encoder_slots(tmp_path):
    import subprocess, sys
    with media_slot('batch', tmp_path), media_slot('batch', tmp_path), media_slot('live', tmp_path):
        for kind in ('batch', 'live'):
            with pytest.raises(MediaBusy):
                with media_slot(kind, tmp_path):
                    pass
        code = "from pathlib import Path; from icsee_ptz.media_runtime import media_slot;\nwith media_slot('batch', Path(__import__('sys').argv[1])): pass"
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, timeout=5)
        assert result.returncode != 0 and b'MediaBusy' in result.stderr
    with media_slot('batch', tmp_path):
        pass


def test_http_video_is_stopped_on_logout_and_slot_is_returned():
    closed = threading.Event()
    def source():
        try:
            while True:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\nfixture\r\n'
        finally:
            closed.set()
    auth = WebAuth('owner', 'dummy-password-only')
    token = auth.issue()
    application = create_app(Config(password='dummy'), manager=Mock(), sd_service=Mock(),
                             video_factory=source, web_auth=auth)
    async def exercise():
        received = asyncio.Event()
        scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.0'},
                 'http_version':'1.1','method':'GET','scheme':'http','path':'/video','raw_path':b'/video',
                 'query_string':b'','root_path':'','server':('127.0.0.1',8000), 'client':('127.0.0.1',123),
                 'headers':[(b'host',b'127.0.0.1:8000'),(b'cookie',(COOKIE+'='+token).encode())]}
        initial = True
        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {'type':'http.request','body':b'','more_body':False}
            await asyncio.Event().wait()
        async def send(message):
            if message['type']=='http.response.body' and message.get('body'):
                received.set()
        task = asyncio.create_task(application(scope, receive, send))
        await asyncio.wait_for(received.wait(), 2)
        auth.revoke(token)
        await asyncio.wait_for(task, 2)
        assert application.state.viewers.count() == 0
        assert closed.is_set()
    asyncio.run(exercise())


def test_account_migration_cli_preserves_password_and_never_prints_it(tmp_path, capsys):
    from icsee_ptz.__main__ import main
    path = tmp_path / 'auth.json'
    path.write_text(json.dumps({'username':'owner','password':'dummy-password-only'}))
    assert main(['accounts','--auth-config',str(path),'migrate']) == 0
    assert 'dummy-password-only' not in path.read_text()
    assert 'dummy-password-only' not in capsys.readouterr().out
    assert WebAuth.from_file(path).credentials_valid('owner','dummy-password-only')
    assert main(['accounts','--auth-config',str(path),'disable','--username','owner']) == 2
    assert WebAuth.from_file(path).credentials_valid('owner','dummy-password-only')


def test_snapshot_does_not_consume_an_extra_viewer_slot():
    from icsee_ptz.media_runtime import ViewerRegistry
    registry = ViewerRegistry()
    for name in ('a','b','c'):
        assert registry.acquire(name,'video')
    assert registry.acquire('a','snapshot')
    assert registry.acquire('a','listen')
    assert registry.count() == 3
    assert not registry.acquire('d','snapshot')
    assert not registry.acquire('a','video')


def test_roles_protect_admin_policy_and_other_users_jobs(tmp_path):
    # The web route is denied before touching storage or executing a camera call.
    auth = WebAuth('owner', 'dummy-password-only')
    for name, role in [('operator1','operator'),('operator2','operator'),('reader','viewer')]:
        auth.users[name] = {'username':name,'role':role,'enabled':True,'password_hash':password_record('dummy-password-only')}
    application = app(auth)
    job = application.state.jobs.submit('render', {'day':'dummy'}, 'operator1')
    with TestClient(application, base_url=ORIGIN) as client:
        login(client, 'operator2')
        assert client.get('/api/jobs/'+job['id']).status_code == 404
        assert client.post('/api/jobs/'+job['id']+'/cancel',headers=HEADERS).status_code == 404
        assert client.get('/api/admin/runtime').status_code == 403
        assert client.post('/api/admin/retention',json={'photo_days':1,'video_days':2},headers=HEADERS).status_code == 403
        login(client, 'reader')
        assert client.get('/api/admin/retention').status_code == 403
