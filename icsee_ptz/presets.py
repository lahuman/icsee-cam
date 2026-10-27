"""Device-backed ONVIF presets; movement requires separate physical verification."""
import threading
import time
from contextlib import contextmanager

from .config import CameraError, ProtocolError
from .onvif_client import ONVIFProbe, PTZ, PTZ_NS


class PresetBusy(CameraError):
    pass


def text_value(value, maximum, label):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CameraError(f'{label} 형식이 올바르지 않습니다.')
    return value


class ONVIFPresets:
    def __init__(self, config):
        config.require_password()
        self.config = config
        self.stopper = None
        self.probe = ONVIFProbe(config, 8899)
        try:
            evidence = self.probe.probe()
            endpoint = evidence.get('resolved_services', {}).get(PTZ_NS)
            profiles = [p for p in evidence.get('profiles', []) if p.get('ptz_configuration')]
            if not evidence.get('endpoint_confirmed') or not endpoint or not profiles:
                raise ProtocolError('ONVIF 프리셋 프로필을 확인하지 못했습니다.')
            # Presets do not require ContinuousMove support.
            profile = profiles[0]
            node = next((n for n in evidence.get('ptz_nodes') or []
                         if n.get('token') == profile['ptz_configuration'].get('NodeToken')), None)
            maximum = node.get('MaximumNumberOfPresets') if node else None
            if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 10000:
                raise ProtocolError('카메라의 프리셋 저장 지원을 확인하지 못했습니다.')
            self.maximum = maximum
            self.profile = profile['profile_token']
            self.ptz = self.probe.service(PTZ, endpoint)
            self.watchdog = self.probe.service(PTZ, endpoint)
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.stopper is not None:
                self.stopper.close()
        finally:
            self.probe.close()

    def stop_motion(self, watchdog=False):
        # This firmware acknowledges ONVIF Stop but continues GotoPreset.
        # Send the physically tested DVRIP STOP first, using independent links
        # for the watchdog, and still attempt both protocols if either fails.
        failed = False
        try:
            if watchdog:
                self.stopper.watchdog.ptz('up', stop=True)
            else:
                self.stopper.stop()
        except Exception:
            failed = True
        try:
            (self.watchdog if watchdog else self.ptz).Stop(
                ProfileToken=self.profile, PanTilt=True, Zoom=True)
        except Exception:
            failed = True
        if failed:
            raise ProtocolError('프리셋 정지 명령을 확인하지 못했습니다. 카메라 상태를 확인해 주세요.')

    def entries(self):
        result = []
        seen = set()
        for entry in self.ptz.GetPresets(ProfileToken=self.profile) or []:
            token = text_value(entry.token, 128, '위치 식별자')
            if token in seen or len(result) >= self.maximum:
                raise ProtocolError('카메라의 프리셋 목록이 올바르지 않습니다.')
            seen.add(token)
            name = getattr(entry, 'Name', None) or token
            text_value(name, 64, '위치 이름')
            result.append({'token': token, 'name': name})
        return result

    def save(self, name, token=None):
        text_value(name, 64, '위치 이름')
        before = self.entries()
        tokens = {p['token'] for p in before}
        if token is not None and text_value(token, 128, '위치 식별자') not in tokens:
            raise CameraError('저장 위치가 없습니다. 목록을 새로고침해 주세요.')
        if token is None and len(before) >= self.maximum:
            raise CameraError('카메라의 저장 위치 개수 제한에 도달했습니다.')
        if any(p['name'].casefold() == name.casefold() and p['token'] != token for p in before):
            raise CameraError('같은 이름의 저장 위치가 있습니다.')
        returned = self.ptz.SetPreset(ProfileToken=self.profile, PresetName=name, PresetToken=token)
        after = self.entries()
        if any(p not in after for p in before if p['token'] != token):
            raise ProtocolError('기존 저장 위치의 보존을 확인하지 못했습니다. 목록을 확인해 주세요.')
        if (not isinstance(returned, str) or (token is None and returned in tokens)
                or (token is not None and returned != token)
                or not any(p == {'token': returned, 'name': name} for p in after)):
            raise ProtocolError('저장 결과를 확인하지 못했습니다. 다시 저장하지 말고 목록을 새로고침해 주세요.')
        return {'state': 'saved', 'presets': after, 'token': returned}

    def remove(self, token):
        text_value(token, 128, '위치 식별자')
        before = self.entries()
        if token not in {p['token'] for p in before}:
            raise CameraError('삭제할 위치가 없습니다. 목록을 새로고침해 주세요.')
        self.ptz.RemovePreset(ProfileToken=self.profile, PresetToken=token)
        after = self.entries()
        if token in {p['token'] for p in after} or any(p not in after for p in before if p['token'] != token):
            raise ProtocolError('삭제 결과를 확인하지 못했습니다. 목록을 새로고침해 주세요.')
        return {'state': 'removed', 'presets': after}

    def move(self, token, cancel):
        text_value(token, 128, '위치 식별자')
        if token not in {p['token'] for p in self.entries()}:
            raise CameraError('이동할 위치가 없습니다. 목록을 새로고침해 주세요.')
        if self.stopper is None:
            from .dvrip_control import DVRIPControl
            self.stopper = DVRIPControl(self.config)
        self.stop_motion()
        self.stop_motion(watchdog=True)
        if cancel.is_set():
            return {'state': 'cancelled_before_move', 'arrival_verified': False}
        stopped = threading.Event()
        failures = []
        def emergency_stop():
            try:
                self.stop_motion(watchdog=True)
                stopped.set()
            except Exception:
                failures.append(True)
        deadline = time.monotonic() + 2
        timer = threading.Timer(2, emergency_stop)
        timer.daemon = True
        try:
            timer.start()
            self.ptz.GotoPreset(ProfileToken=self.profile, PresetToken=token)
            while time.monotonic() < deadline and not cancel.wait(.02):
                pass
        finally:
            timer.cancel()
            timer.join()
            try:
                self.stop_motion()
                stopped.set()
            except Exception:
                failures.append(True)
        if failures or not stopped.is_set():
            raise ProtocolError('프리셋 이동 후 정지를 확인하지 못했습니다. 화면과 카메라 상태를 확인해 주세요.')
        return {'state': 'stopped', 'arrival_verified': False,
                'cancelled': cancel.is_set(), 'message': '이동 요청 후 정지했습니다. 목표 위치 도달은 영상으로 확인해 주세요.'}


class PresetService:
    def __init__(self, config, verified, operation_lock, factory=None, identity_factory=None):
        from .dvrip import DVRIPClient
        self.config, self.verified, self.operation_lock = config, verified, operation_lock
        self.factory = factory or (lambda: ONVIFPresets(config))
        self.identity_factory = identity_factory or (lambda: DVRIPClient(config))
        self.movement_verified = bool(verified and verified.get('preset_protocol') == 'onvif'
                                     and verified.get('preset_stop_protocol') == 'dvrip'
                                     and verified.get('preset_motion_and_stop') == 'confirmed_by_user')
        self.motion_cancel = None

    @contextmanager
    def controller(self, write=False, cancel=None):
        if not self.operation_lock.acquire(blocking=False):
            raise PresetBusy('다른 카메라 제어를 처리 중입니다. 잠시 후 다시 시도해 주세요.')
        camera = None
        if cancel is not None:
            self.motion_cancel = cancel
        try:
            with self.identity_factory() as identity:
                info = identity.read('SystemInfo')
                if (not self.verified or info.get('HardWare') != self.verified['hardware']
                        or info.get('SoftWareVersion') != self.verified['firmware']):
                    raise ProtocolError('검증 기록과 실제 카메라 또는 펌웨어가 다릅니다.')
                if write and bool(identity.tracking()['Enable']):
                    raise CameraError('자동 추적을 끈 뒤 프리셋 저장·이동·삭제를 실행해 주세요.')
            camera = self.factory()
            yield camera
        finally:
            try:
                if camera:
                    camera.close()
            finally:
                if cancel is not None:
                    self.motion_cancel = None
                self.operation_lock.release()

    def list(self):
        with self.controller() as camera:
            return {'presets': camera.entries(), 'maximum': camera.maximum,
                    'movement_verified': self.movement_verified, 'required_role': 'admin'}

    def save(self, name, token=None):
        text_value(name, 64, '위치 이름')
        if token is not None:
            text_value(token, 128, '위치 식별자')
        with self.controller(write=True) as camera:
            return camera.save(name, token)

    def remove(self, token):
        text_value(token, 128, '위치 식별자')
        with self.controller(write=True) as camera:
            return camera.remove(token)

    def move(self, token, cancel):
        if not self.movement_verified:
            raise CameraError('위치 이동은 실제 도달·정지 검수가 끝난 뒤 활성화됩니다.')
        with self.controller(write=True, cancel=cancel) as camera:
            return camera.move(token, cancel)

    def stop(self):
        cancel = self.motion_cancel
        if cancel is not None:
            cancel.set()
            return True
        return False
