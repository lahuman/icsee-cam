import asyncio
import threading
import time
from types import SimpleNamespace as N
from unittest.mock import Mock, MagicMock
import pytest
from fastapi.testclient import TestClient
from icsee_ptz.config import Config, CameraError, ProtocolError
from icsee_ptz.presets import ONVIFPresets, PresetService, PresetBusy
from icsee_ptz.web import create_app, MovementManager
from icsee_ptz.web_auth import WebAuth
from icsee_ptz.accounts import password_record
from icsee_ptz.verification import verified_profile
from icsee_ptz.security import required_role, request_category

HEADERS={'Origin':'http://127.0.0.1:8000','X-ICSEE-Control':'local'}


def controller():
    c=object.__new__(ONVIFPresets)
    c.ptz=Mock();c.watchdog=Mock();c.stopper=Mock();c.profile='profile';c.maximum=2
    c.ptz.GetPresets.return_value=[]
    return c


def test_save_and_remove_require_readback():
    c=controller()
    c.ptz.GetPresets.side_effect=[[],[N(token='one',Name='현관')]]
    c.ptz.SetPreset.return_value='one'
    assert c.save('현관')['state']=='saved'
    c.ptz.SetPreset.assert_called_once_with(ProfileToken='profile',PresetName='현관',PresetToken=None)
    c.ptz.GetPresets.side_effect=[[N(token='one',Name='현관')],[]]
    assert c.remove('one')['state']=='removed'
    c.ptz.GetPresets.side_effect=[[],[]]
    with pytest.raises(ProtocolError):c.save('현관')


@pytest.mark.parametrize('name',['',' ', ' name', 'x\n', 'x'*65,123])
def test_invalid_name_never_contacts_camera(name):
    c=controller()
    with pytest.raises(CameraError):c.save(name)
    c.ptz.GetPresets.assert_not_called();c.ptz.SetPreset.assert_not_called()


def test_duplicate_capacity_unknown_token_never_write():
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='Entry'),N(token='two',Name='Other')]
    for name,token in [('entry',None),('third',None),('replacement','unknown')]:
        with pytest.raises(CameraError):c.save(name,token)
    with pytest.raises(CameraError):c.remove('unknown')
    c.ptz.SetPreset.assert_not_called();c.ptz.RemovePreset.assert_not_called()


def test_overwrite_requires_same_token_and_confirmed_name():
    c=controller();c.ptz.GetPresets.side_effect=[[N(token='one',Name='before')],[N(token='one',Name='after')]]
    c.ptz.SetPreset.return_value='one'
    assert c.save('after','one')['token']=='one'
    c.ptz.GetPresets.side_effect=[[N(token='one',Name='after')],[N(token='one',Name='after')]]
    with pytest.raises(ProtocolError):c.remove('one')


def service():
    c=Mock();c.maximum=100;c.entries.return_value=[]
    identity=MagicMock();identity.__enter__.return_value=identity
    verified=dict(verified_profile(Config()))
    for key in ['preset_protocol', 'preset_stop_protocol', 'preset_motion_and_stop']:
        verified.pop(key, None)
    identity.read.return_value={'HardWare':verified['hardware'],'SoftWareVersion':verified['firmware']}
    identity.tracking.return_value={'Enable':0}
    lock=threading.Lock()
    s=PresetService(Config(password='dummy'),verified,lock,lambda:c,lambda:identity)
    return s,c,identity,lock


def test_identity_tracking_and_shared_lock_gate_changes():
    s,c,identity,lock=service()
    assert s.list()['movement_verified'] is False
    identity.tracking.return_value={'Enable':1}
    with pytest.raises(CameraError,match='자동 추적'):s.save('name')
    c.save.assert_not_called()
    with lock:
        with pytest.raises(PresetBusy):s.list()
    identity.read.return_value={'HardWare':'wrong'}
    with pytest.raises(ProtocolError):s.list()


def test_unverified_movement_never_contacts_camera():
    s,c,identity,lock=service()
    with pytest.raises(CameraError,match='검수'):s.move('one',threading.Event())
    identity.read.assert_not_called();c.move.assert_not_called()


@pytest.mark.parametrize('stop_protocol,expected',[(None,False),('onvif',False),('dvrip',True)])
def test_movement_requires_verified_dvrip_stop(stop_protocol,expected):
    verified={'preset_protocol':'onvif','preset_motion_and_stop':'confirmed_by_user',
              'preset_stop_protocol':stop_protocol}
    s=PresetService(Config(password='dummy'),verified,threading.Lock())
    assert s.movement_verified is expected


def test_stop_during_discovery_cancels_before_dispatch():
    s,c,identity,lock=service();s.movement_verified=True
    cancel=threading.Event()
    def read(_):
        assert s.stop()
        return {'HardWare':s.verified['hardware'],'SoftWareVersion':s.verified['firmware']}
    identity.read.side_effect=read
    c.move.side_effect=lambda token,event: {'cancelled':event.is_set()}
    assert s.move('one',cancel)['cancelled']
    assert not s.stop() and not lock.locked()


def test_goto_stop_and_error_cleanup():
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='name')]
    cancel=threading.Event();cancel.set()
    assert c.move('one',cancel)['state']=='cancelled_before_move'
    c.ptz.GotoPreset.assert_not_called()
    cancel.clear();c.ptz.GotoPreset.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):c.move('one',cancel)
    assert c.ptz.Stop.call_count==3
    c.ptz.GotoPreset.side_effect=lambda **_:cancel.set()
    assert c.move('one',cancel)['arrival_verified'] is False


def test_independent_watchdog_when_goto_blocks(monkeypatch):
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='name')]
    original_timer=threading.Timer
    monkeypatch.setattr('icsee_ptz.presets.threading.Timer',lambda seconds,fn:original_timer(.03,fn))
    cancel=threading.Event()
    def slow(**_):
        time.sleep(.08)
        assert c.watchdog.Stop.call_count==2
        assert c.stopper.watchdog.ptz.call_count==2
        cancel.set()
    c.ptz.GotoPreset.side_effect=slow
    assert c.move('one',cancel)['state']=='stopped'
    assert c.stopper.stop.call_count==2


@pytest.mark.parametrize('failed_protocol',['onvif','dvrip'])
def test_both_stop_protocols_attempted_and_failure_blocks_goto(failed_protocol):
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='name')]
    failed=c.ptz.Stop if failed_protocol=='onvif' else c.stopper.stop
    failed.side_effect=TimeoutError()
    with pytest.raises(ProtocolError,match='정지'):
        c.move('one',threading.Event())
    c.stopper.stop.assert_called_once();c.ptz.Stop.assert_called_once()
    c.ptz.GotoPreset.assert_not_called()


def test_cancel_after_dispatch_sends_dvrip_stop_before_onvif():
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='name')]
    calls=[];cancel=threading.Event()
    c.stopper.stop.side_effect=lambda:calls.append('dvrip')
    c.ptz.Stop.side_effect=lambda **_:calls.append('onvif')
    c.ptz.GotoPreset.side_effect=lambda **_:cancel.set()
    assert c.move('one',cancel)['cancelled']
    assert calls==['dvrip','onvif','dvrip','onvif']


def test_final_dvrip_stop_failure_is_not_success():
    c=controller();c.ptz.GetPresets.return_value=[N(token='one',Name='name')]
    cancel=threading.Event();c.ptz.GotoPreset.side_effect=lambda **_:cancel.set()
    c.stopper.stop.side_effect=[None,TimeoutError()]
    with pytest.raises(ProtocolError,match='정지'):
        c.move('one',cancel)
    assert c.ptz.Stop.call_count==2


def test_manual_ptz_uses_shared_operation_lock():
    factory=Mock();manager=MovementManager(factory)
    with manager.operation_lock:
        with pytest.raises(CameraError,match='다른'):manager.start('left',.25)
    factory.assert_not_called()


def application():
    auth=WebAuth('admin','dummy-password')
    for role in ['viewer','operator']:
        auth.users[role]={'username':role,'role':role,'enabled':True,'password_hash':password_record('dummy-password')}
    s=Mock();s.list.return_value={'presets':[],'maximum':100,'movement_verified':False}
    s.save.return_value={'state':'saved','presets':[]};s.remove.return_value={'state':'removed','presets':[]}
    s.move.return_value={'state':'stopped','arrival_verified':False}
    app=create_app(Config(password='dummy'),manager=Mock(),web_auth=auth,preset_service=s)
    return app,s


@pytest.mark.parametrize('role',['viewer','operator','admin'])
def test_all_preset_endpoints_are_admin_only(role):
    app,s=application()
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',12345)) as client:
        assert client.post('/api/login',json={'username':role,'password':'dummy-password'},headers=HEADERS).status_code==200
        calls=[('GET','',None),('POST','/save',{'name':'entry'}),('POST','/goto',{'token':'one'}),('POST','/remove',{'token':'one'}),('POST','/stop',{})]
        for method,suffix,body in calls:
            r=client.request(method,'/api/admin/presets'+suffix,json=body,headers=HEADERS)
            assert r.status_code==(200 if role=='admin' else 403)
        if role!='admin':assert not s.mock_calls


def test_invalid_payload_origin_and_stop_budget():
    app,s=application()
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',12345)) as client:
        client.post('/api/login',json={'username':'admin','password':'dummy-password'},headers=HEADERS)
        for body in [{'name':123},{'name':'entry','extra':True},{'name':''}]:
            assert client.post('/api/admin/presets/save',json=body,headers=HEADERS).status_code==422
        assert client.post('/api/admin/presets/save',json={'name':'entry'},headers={**HEADERS,'Origin':'http://evil.example'}).status_code==403
        s.save.assert_not_called()
    assert required_role('/api/admin/presets','GET')=='admin'
    assert request_category('/api/admin/presets/stop','POST') is None


def test_expiry_cancels_inflight_move_and_waits_for_stop():
    app,s=application();auth=app.state.auth
    started=threading.Event();stopped=threading.Event()
    def moving(token,cancel):
        started.set()
        assert cancel.wait(3)
        stopped.set()
        return {'state':'stopped'}
    s.move.side_effect=moving
    token=auth.issue('admin')
    async def exercise():
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.0'},'http_version':'1.1',
               'method':'POST','scheme':'http','path':'/api/admin/presets/goto','raw_path':b'/api/admin/presets/goto',
               'query_string':b'','root_path':'','server':('127.0.0.1',8000),'client':('127.0.0.1',123),
               'headers':[(b'host',b'127.0.0.1:8000'),(b'cookie',('icsee_session='+token).encode()),
                          (b'content-type',b'application/json'),(b'x-icsee-control',b'local')]}
        delivered=False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':b'{"token":"one"}','more_body':False}
            await asyncio.Event().wait()
        messages=[]
        async def send(message):messages.append(message)
        task=asyncio.create_task(app(scope,receive,send))
        assert await asyncio.to_thread(started.wait,2)
        auth.sessions[token]=0
        await asyncio.wait_for(task,3)
        assert stopped.is_set()
        assert next(m['status'] for m in messages if m['type']=='http.response.start')==401
    asyncio.run(exercise())
