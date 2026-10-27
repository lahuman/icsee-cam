from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from icsee_ptz.config import Config,CameraError
from icsee_ptz.web import create_app
from icsee_ptz.web_access import WebAccess

LAN='192.168.1.159:8000'

def test_lan_policy_is_opt_in_and_exact():
    local=WebAccess(8000)
    assert LAN not in local.hosts
    access=WebAccess(8000,lan_ip='192.168.1.159')
    assert LAN in access.hosts
    assert access.client_allowed('192.168.1.88')
    assert not access.client_allowed('192.168.219.88')
    assert not access.client_allowed('8.8.8.8')
    assert access.origin_allowed(LAN,'http://'+LAN)
    assert not access.origin_allowed(LAN,'http://192.168.1.88:8000')
    with pytest.raises(CameraError):WebAccess(8000,lan_ip='8.8.8.8')


def test_lan_page_archive_and_same_origin_control():
    manager=Mock();manager.status.return_value={}
    app=create_app(allow_unauthenticated=True, config=Config(password='lan-secret-test-only'),manager=manager,lan_ip='192.168.1.159',container_mode=True)
    with TestClient(app,base_url='http://'+LAN,client=('172.18.0.1',123)) as client:
        page=client.get('/')
        assert page.status_code==200
        assert 'ws://'+LAN in page.headers['content-security-policy']
        assert client.get('/api/status').status_code==200
        assert client.get('/',headers={'Host':'evil.example'}).status_code==403
        assert client.post('/api/check',headers={'Origin':'http://'+LAN,'X-ICSEE-Control':'local'}).status_code==200
        assert client.post('/api/check',headers={'Origin':'http://evil.example','X-ICSEE-Control':'local'}).status_code==403
        assert 'lan-secret-test-only' not in page.text
    manager.start.assert_not_called()


def test_lan_listen_websocket_validates_host_origin_and_streams():
    async def stream():yield b'\0'*3200
    manager=Mock();manager.status.return_value={}
    app=create_app(allow_unauthenticated=True, config=Config(password='test'),manager=manager,lan_ip='192.168.1.159',listen_factory=stream,container_mode=True)
    with TestClient(app,base_url='http://'+LAN,client=('172.18.0.1',123)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/audio/listen',headers={'host':LAN,'origin':'http://evil.example'}):pass
        with client.websocket_connect('/audio/listen',headers={'host':LAN,'origin':'http://'+LAN}) as ws:
            assert ws.receive_json()['state']=='ready'
            assert len(ws.receive_bytes())==3200
