"""Memory-only RTSP listening and dedicated DVRIP G.711 A-law talkback."""
import asyncio
from bisect import bisect_right
import json
import shutil
import socket
import struct
import threading
import time

from .config import CameraError, ProtocolError
from .dvrip import DVRIPClient, HEADER
from .video import rtsp_url

PCM_PACKET_BYTES = 640  # 320 signed 16-bit samples: 40 ms at 8 kHz.
MAX_TALK_SECONDS = 15
_ALAW_LEVELS = [((16 * (i & 15) + 8) if i < 16 else
                 (16 * (i & 15) + 264) << ((i >> 4) - 1)) for i in range(128)]
_ALAW_BOUNDARIES = [(_ALAW_LEVELS[i] + _ALAW_LEVELS[i + 1] + 4) // 8 for i in range(127)]


def pcm16_to_alaw(pcm):
    if len(pcm) % 2:
        raise ProtocolError("PCM 프레임은 16비트 샘플이어야 합니다.")
    output = bytearray()
    for (sample,) in struct.iter_unpack("<h", pcm):
        mask = 0xD5 if sample >= 0 else 0x55
        # Nearest A-law reconstruction level, matching FFmpeg's 14-bit rounding.
        output.append(bisect_right(_ALAW_BOUNDARIES, abs(sample >> 2)) ^ mask)
    return bytes(output)


class DVRIPTalk:
    def __init__(self, config):
        self.client = DVRIPClient(config)
        self.closed = threading.Event()
        self.write_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.reader = None
        self.expiry = None
        self.started = None
        self.packets = 0
        self.start_acknowledged = False
        self.failure = None
        self.last_input = None
        self.watchdog = None

    def open(self):
        try:
            self.client.__enter__()
            if self.closed.is_set():
                raise CameraError("말하기 요청이 취소되었습니다.")
            reply = self.client._exchange(1434, {
                "Name": "OPTalk", "SessionID": f"0x{self.client.session:08X}",
                "OPTalk": {"Action": "Claim", "AudioFormat": {"EncodeType": "G711_ALAW"}}})
            if reply.get("Ret") != 100:
                raise ProtocolError(f"DVRIP 음성 채널 요청이 거부됐습니다 (Ret={reply.get('Ret')}).")
            self._write(1430, json.dumps({"Name": "OPTalk",
                "SessionID": f"0x{self.client.session:08X}",
                "OPTalk": {"Action": "Start", "AudioFormat": {"EncodeType": "G711_ALAW"}}}).encode() + b"\n\0")
            # The camera returns microphone frames on the talk socket. Discard these
            # in memory so reverse audio cannot fill the socket and stall transmission.
            self.reader = threading.Thread(target=self._drain, daemon=True)
            self.reader.start()
            self.started = time.monotonic()
            self.last_input = self.started
            self.expiry = threading.Timer(MAX_TALK_SECONDS, self.close)
            self.expiry.daemon = True
            self.expiry.start()
            self.watchdog = threading.Thread(target=self._watch_inactivity, daemon=True)
            self.watchdog.start()
            return {"state": "ready", "sample_rate": 8000, "pcm_packet_bytes": PCM_PACKET_BYTES,
                    "max_seconds": MAX_TALK_SECONDS, "claim_ret": 100,
                    "start_acknowledged": False}
        except BaseException:
            self.close()
            raise

    def _write(self, message, body):
        if message not in (1430, 1432):
            raise ProtocolError("허용되지 않은 음성 메시지입니다.")
        with self.write_lock:
            if self.closed.is_set() or self.client.sock is None:
                raise CameraError("음성 송출 연결이 종료됐습니다.")
            self.client.sock.sendall(HEADER.pack(255, 0, self.client.session, self.client.sequence,
                                               message, len(body)) + body)
            self.client.sequence += 1

    def send_pcm(self, pcm):
        if self.failure:
            raise self.failure
        if len(pcm) != PCM_PACKET_BYTES:
            raise ProtocolError("말하기 PCM 프레임 크기가 올바르지 않습니다.")
        if self.started is None or time.monotonic() - self.started >= MAX_TALK_SECONDS:
            raise CameraError("한 번의 말하기는 최대 15초입니다.")
        # Permit at most 160 ms of pre-buffering; never queue seconds of speech.
        if (self.packets + 1) * 0.04 - (time.monotonic() - self.started) > 0.20:
            raise ProtocolError("음성이 실시간 속도보다 빠르게 입력됐습니다.")
        body = struct.pack(">I", 0x1FA) + bytes([14, 2]) + struct.pack("<H", 320)
        self.last_input = time.monotonic()
        self._write(1432, body + pcm16_to_alaw(pcm))
        self.packets += 1

    def _watch_inactivity(self):
        while not self.closed.wait(0.1):
            if time.monotonic() - self.last_input >= 1:
                self.close()
                return

    def _drain(self):
        def read_exact(length):
            data = bytearray()
            while len(data) < length and not self.closed.is_set():
                try:
                    chunk = self.client.sock.recv(length - len(data))
                except socket.timeout:
                    continue
                if not chunk:
                    raise OSError("closed")
                data.extend(chunk)
            if len(data) != length:
                raise OSError("closed")
            return bytes(data)
        while not self.closed.is_set():
            try:
                magic, _, _, _, message, length = HEADER.unpack(read_exact(20))
                if magic != 255 or length > 1024 * 1024:
                    raise OSError("invalid reply")
                body = read_exact(length)
                if message == 1431 and body.lstrip().startswith(b"{"):
                    reply = json.loads(body.rstrip(b"\x00\r\n "))
                    if reply.get("Ret") == 100:
                        self.start_acknowledged = True
                    elif reply.get("Ret") is not None:
                        self.failure = ProtocolError("DVRIP 음성 시작 요청이 거부됐습니다.")
                        break
            except (OSError, AttributeError, ValueError):
                break
        self.close()

    def close(self):
        with self.close_lock:
            self.closed.set()
            if self.expiry:
                self.expiry.cancel()
            sock = self.client.sock
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.client.close()
        # Upstream ends talk by closing the dedicated connection, without inventing
        # a firmware-specific Stop action. Already buffered audio cannot be recalled.
        if self.reader and threading.current_thread() is not self.reader:
            self.reader.join(timeout=0.5)


async def listen_pcm(config):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CameraError("듣기에는 ffmpeg가 필요합니다.")
    process = await asyncio.create_subprocess_exec(ffmpeg, "-hide_banner", "-loglevel", "quiet",
        "-nostdin", "-rtsp_transport", "tcp", "-timeout", "5000000", "-i", rtsp_url(config),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        while True:
            try:
                block = await asyncio.wait_for(process.stdout.readexactly(3200), timeout=8)
            except asyncio.IncompleteReadError:
                raise ConnectionError("카메라 오디오 스트림이 종료됐습니다.") from None
            yield block
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
