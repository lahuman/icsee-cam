"""Small plaintext DVRIP client; wire format verified against upstream sources.

Login, allowlisted reads, tracking/clock settings and explicitly invoked PTZ.
"""

import hashlib
import json
import socket
import struct
import time
from datetime import datetime

from .config import AuthenticationError, ProtocolError

HEADER = struct.Struct("<BB2xII2xHI")
MAX_BODY = 1024 * 1024
READS = {"SystemInfo": 1020, "SystemFunction": 1360,
         "StorageInfo": 1020, "WorkState": 1020,
         "Record": 1042, "Detect.MotionDetect": 1042, "Simplify.Encode": 1042,
         "Detect.DetectTrack": 1042, "OPTimeQuery": 1452}


def sofia_hash(password):
    digest = hashlib.md5(password.encode("utf-8")).digest()
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(alphabet[(digest[i] + digest[i + 1]) % 62]
                   for i in range(0, 16, 2))


class DVRIPClient:
    def __init__(self, config):
        self.config = config
        self.sock = None
        self.session = 0
        self.sequence = 0

    def __enter__(self):
        self.config.require_password()
        self.sock = socket.create_connection((self.config.host, 34567), self.config.timeout)
        self.sock.settimeout(self.config.timeout)
        try:
            reply = self._exchange(1000, {
                "EncryptType": "MD5", "LoginType": "DVRIP-Web",
                "UserName": self.config.username, "PassWord": sofia_hash(self.config.password),
            })
            code = reply.get("Ret")
            if code in (103, 105, 106, 107, 203):
                raise AuthenticationError(f"DVRIP 인증/권한 오류 (Ret={code}); 재시도 중단.")
            if code != 100:
                raise ProtocolError("DVRIP 로그인 성공 응답이 아닙니다.")
            try:
                session = int(reply["SessionID"], 16)
            except (KeyError, TypeError, ValueError):
                raise ProtocolError("DVRIP 세션 응답이 올바르지 않습니다.") from None
            if session != self.session:
                raise ProtocolError("DVRIP 헤더/로그인 세션이 일치하지 않습니다.")
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _receive(self, length):
        data = bytearray()
        while len(data) < length:
            remaining = getattr(self, "_deadline", time.monotonic() + self.config.timeout) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DVRIP 응답 제한 시간을 초과했습니다.")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise ProtocolError("DVRIP 연결이 응답 도중 종료되었습니다.")
            data.extend(chunk)
        return bytes(data)

    def _exchange(self, message, data):
        if message == 1450:
            # Reboot uses the same message ID. Allow only the clock operation.
            if data.get("Name") != "OPTimeSetting":
                raise ProtocolError("시간 동기화 이외의 시스템 변경은 허용되지 않습니다.")
            try:
                datetime.strptime(data.get("OPTimeSetting"), "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                raise ProtocolError("카메라 시간 형식이 올바르지 않습니다.") from None
        elif message == 1040:
            if data.get("Name") != "Detect.DetectTrack":
                raise ProtocolError("자동 추적 이외의 설정 변경은 허용되지 않습니다.")
        elif message not in (1000, 1400, 1434, 1440, 1424, *READS.values()):
            raise ProtocolError("허용되지 않은 DVRIP 명령입니다.")
        body = json.dumps(data, ensure_ascii=False).encode() + b"\x0a\x00"
        self._deadline = time.monotonic() + self.config.timeout
        sequence = self.sequence
        self.sock.sendall(HEADER.pack(255, 0, self.session, sequence, message, len(body)) + body)
        self.sequence += 1
        magic, version, session, seq, msg, length = HEADER.unpack(self._receive(HEADER.size))
        # This camera replies with version=1 while login bodies remain plain JSON.
        # The version byte alone does not establish encrypted-body support.
        sequence_ok = seq == sequence or (message in (1434, 1424) and seq == sequence + 1)
        # Observed firmware increments the sequence in its OPTalk Claim reply.
        if magic != 255 or version not in (0, 1) or msg != message + 1 or not sequence_ok:
            raise ProtocolError("DVRIP 헤더/시퀀스/전송 버전이 예상과 다릅니다.")
        if self.session and session != self.session:
            raise ProtocolError("DVRIP 응답 세션이 변경되었습니다.")
        if not 0 < length <= MAX_BODY:
            raise ProtocolError("DVRIP 응답이 비었거나 크기 제한을 초과했습니다.")
        raw = self._receive(length).rstrip(b"\x00\r\n ")
        if not raw.startswith(b"{"):
            raise ProtocolError("일반 JSON 응답이 아닙니다. 암호화 전송 요구 가능성이 있습니다.")
        try:
            reply = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ProtocolError("DVRIP JSON 응답이 올바르지 않습니다.") from None
        if not isinstance(reply, dict):
            raise ProtocolError("DVRIP 객체 응답이 아닙니다.")
        self.session = session
        return reply

    def read(self, name):
        if name not in READS:
            raise ProtocolError("허용되지 않은 DVRIP 조회입니다.")
        reply = self._exchange(READS[name], {"Name": name, "SessionID": f"0x{self.session:08X}"})
        if reply.get("Ret") in (103, 105, 106, 107, 203):
            raise AuthenticationError("DVRIP 조회 권한이 없거나 세션이 만료되었습니다.")
        if reply.get("Ret") != 100 or name not in reply:
            raise ProtocolError("DVRIP 조회 성공/데이터가 확인되지 않았습니다.")
        return reply[name]

    def camera_time(self):
        value = self.read("OPTimeQuery")
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            raise ProtocolError("카메라의 시간 응답 형식을 확인할 수 없습니다.") from None

    def set_camera_time(self, value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ProtocolError("카메라 시간 설정에는 시간대가 지정된 현재 시간이 필요합니다.")
        reply = self._exchange(1450, {"Name": "OPTimeSetting",
            "SessionID": f"0x{self.session:08X}",
            "OPTimeSetting": value.strftime("%Y-%m-%d %H:%M:%S")})
        if reply.get("Ret") in (103, 105, 106, 107, 203):
            raise AuthenticationError("카메라 시간을 변경할 권한이 없습니다.")
        if reply.get("Ret") != 100:
            raise ProtocolError("카메라 시간 변경을 확인하지 못했습니다. 현재 시간을 다시 조회해 주세요.")

    def tracking(self):
        data = self.read("Detect.DetectTrack")
        if (not isinstance(data, dict) or type(data.get("Enable")) not in (int, bool)
                or data["Enable"] not in (0, 1)):
            raise ProtocolError("카메라의 자동 추적 설정 형식을 확인할 수 없습니다.")
        return data

    def set_tracking(self, enabled):
        if type(enabled) is not bool:
            raise ProtocolError("자동 추적 설정은 켜기 또는 끄기여야 합니다.")
        current = self.tracking()
        if bool(current["Enable"]) == enabled:
            return {"enabled": enabled}
        # Read immediately before writing; retain sensitivity, return time and
        # any firmware-specific fields. Only Enable is changed by the web UI.
        desired = {**current, "Enable": int(enabled)}
        name = "Detect.DetectTrack"
        reply = self._exchange(1040, {"Name": name,
            "SessionID": f"0x{self.session:08X}", name: desired})
        if reply.get("Ret") in (103, 105, 106, 107, 203):
            raise AuthenticationError("자동 추적 설정을 변경할 권한이 없습니다.")
        if reply.get("Ret") != 100:
            raise ProtocolError("자동 추적 설정 변경을 확인하지 못했습니다. 상태를 다시 조회해 주세요.")
        actual = self.tracking()
        if bool(actual["Enable"]) != enabled:
            raise ProtocolError("요청한 자동 추적 상태와 카메라 값이 다릅니다. 상태를 다시 조회해 주세요.")
        return {"enabled": bool(actual["Enable"])}

    def ptz(self, direction, *, stop=False, step=1, captured_shape=False, legacy_start=False):
        commands = {"left": "DirectionLeft", "right": "DirectionRight",
                    "up": "DirectionUp", "down": "DirectionDown"}
        if direction not in commands or not isinstance(step, int) or not 1 <= step <= 8:
            raise ProtocolError("DVRIP 방향/속도가 올바르지 않습니다.")
        parameter = {"AUX": {"Number": 0, "Status": "On"}, "Channel": 0,
                     "MenuOpts": "Enter", "Pattern": "Start", "Preset": -1 if stop else 65535,
                     "Step": 5 if stop else step, "Tour": 0}
        if captured_shape:
            # Actual captured vendor packet documented in research/SOURCES.md.
            parameter["Pattern"] = "SetBegin"
            parameter["POINT"] = {"bottom": 0, "left": 0, "right": 0, "top": 0}
        if legacy_start and not stop:
            # dbuezas v4.3.0 async_move defaults to preset=0 (not GotoPreset).
            parameter["Preset"] = 0
        reply = self._exchange(1400, {
            "Name": "OPPTZControl", "SessionID": f"0x{self.session:08X}",
            "OPPTZControl": {"Command": "DirectionUp" if stop else commands[direction],
                "Parameter": parameter}})
        if reply.get("Ret") != 100:
            raise ProtocolError("DVRIP PTZ 성공 응답이 아닙니다.")
        return {"ret": 100}
