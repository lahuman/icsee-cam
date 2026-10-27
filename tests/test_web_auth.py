import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from icsee_ptz.config import Config, CameraError
from icsee_ptz.web import create_app
from icsee_ptz.web_auth import WebAuth, COOKIE

PASSWORD = 'web-test-password-only'
HEADERS = {'Origin': 'http://192.168.1.159:8000', 'X-ICSEE-Control': 'local'}


def setup_client():
    auth = WebAuth('owner', PASSWORD)
    manager = Mock()
    manager.status.return_value = {}
    factory = Mock()
    app = create_app(allow_unauthenticated=True, config=Config(password='camera-test-secret'), manager=manager,
                     video_factory=factory, talk_factory=factory, listen_factory=factory,
                     container_mode=True, lan_ip='192.168.1.159', web_auth=auth)
    return TestClient(app, base_url='http://192.168.1.159:8000'), auth, factory, manager


def login(client, password=PASSWORD):
    return client.post('/api/login', json={'username': 'owner', 'password': password}, headers=HEADERS)


def test_login_protects_every_resource_and_websocket_without_camera_access():
    client, auth, factory, manager = setup_client()
    with client:
        assert client.get('/', follow_redirects=False).headers['location'] == '/login'
        assert client.get('/login').status_code == 200
        assert PASSWORD not in client.get('/login.js').text
        for path in ['/api/status', '/video', '/snapshot', '/audio.js', '/session.js', '/camera.css',
                     '/tracking.js', '/api/settings/tracking',
                     '/api/timelapse/days', '/api/timelapse/video/fake', '/api/sd/preview/fake']:
            assert client.get(path).status_code == 401
        assert client.post('/api/check', headers=HEADERS).status_code == 401
        assert client.post('/api/settings/tracking', json={'enabled': True}, headers=HEADERS).status_code == 401
        for path in ['/audio/listen', '/audio/talk']:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path, headers={'Origin': HEADERS['Origin']}):
                    pass
        assert client.get('/healthz').json() == {'status': 'ok'}
        factory.assert_not_called()
        manager.check.assert_not_called()


def test_login_cookie_logout_revocation_expiry_and_guards():
    client, auth, factory, manager = setup_client()
    with client:
        assert login(client, 'incorrect-password').status_code == 401
        result = login(client)
        assert result.status_code == 200
        assert 'httponly' in result.headers['set-cookie'].lower()
        assert 'samesite=strict' in result.headers['set-cookie'].lower()
        token = client.cookies.get(COOKIE)
        assert client.get('/').status_code == 200
        assert client.get('/camera.css').status_code == 200
        assert client.get('/api/status').status_code == 200
        assert client.post('/api/logout', headers={**HEADERS, 'Origin': 'http://evil.example'}).status_code == 403
        assert client.post('/api/logout', headers=HEADERS).status_code == 200
        client.cookies.set(COOKIE, token)
        assert client.get('/api/status').status_code == 401
        client.cookies.clear()
        assert login(client).status_code == 200
        auth.sessions[client.cookies.get(COOKIE)] = 0
        assert client.get('/api/status').status_code == 401


def test_authenticated_websocket_gets_past_auth_but_origin_still_required():
    client, auth, factory, manager = setup_client()
    async def stream():
        yield b'\0' * 3200
    factory.side_effect = stream
    with client:
        login(client)
        with client.websocket_connect('ws://192.168.1.159:8000/audio/listen', headers={'Origin': HEADERS['Origin']}) as ws:
            assert ws.receive_json()['state'] == 'ready'
            assert len(ws.receive_bytes()) == 3200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('ws://192.168.1.159:8000/audio/listen', headers={'Origin': 'http://evil.example'}):
                pass


def test_login_rate_limit_and_oversize_body():
    client, auth, _, _ = setup_client()
    with client:
        assert client.post('/api/login', content='x' * 5000, headers=HEADERS).status_code == 413
        for _ in range(19):
            assert client.post('/api/login', json={}, headers=HEADERS).status_code == 400
        assert login(client).status_code == 429


def test_config_is_required_and_validated(tmp_path):
    path = tmp_path / 'auth.json'
    with pytest.raises(CameraError): WebAuth.from_file(path)
    path.write_text(json.dumps({'username': 'owner', 'password': 'short'}))
    with pytest.raises(CameraError): WebAuth.from_file(path)
    path.write_text(json.dumps({'username': 'owner', 'password': PASSWORD}))
    assert WebAuth.from_file(path).credentials_valid('owner', PASSWORD)


def test_https_proxy_domain_login_cookie_origin_and_websocket():
    auth = WebAuth('owner', PASSWORD)
    manager = Mock(); manager.status.return_value = {}
    async def stream(): yield b'\0' * 3200
    app = create_app(allow_unauthenticated=True, config=Config(password='camera-test-secret'), manager=manager,
                     listen_factory=stream, container_mode=True, web_auth=auth,
                     public_origin='https://l4u.duckdns.org')
    headers = {'Origin': 'https://l4u.duckdns.org', 'X-ICSEE-Control': 'local'}
    # The reverse proxy forwards plain HTTP to the container, keeping Host/Origin.
    with TestClient(app, base_url='https://l4u.duckdns.org') as client:
        response = client.post('/api/login', json={'username': 'owner', 'password': PASSWORD}, headers=headers)
        assert response.status_code == 200
        assert 'secure' in response.headers['set-cookie'].lower()
        page = client.get('/')
        assert page.status_code == 200
        assert 'wss://l4u.duckdns.org' in page.headers['content-security-policy']
        assert client.post('/api/logout', headers={**headers, 'Origin': 'http://l4u.duckdns.org'}).status_code == 403
        with client.websocket_connect('wss://l4u.duckdns.org/audio/listen', headers={'Origin': headers['Origin']}) as ws:
            assert ws.receive_json()['state'] == 'ready'
        assert client.get('/', headers={'Host': 'other.duckdns.org'}).status_code == 403
        assert client.post('/api/logout', headers=headers).status_code == 200
