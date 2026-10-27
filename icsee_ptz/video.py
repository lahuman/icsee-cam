"""Server-side RTSP to MJPEG; never expose the camera URL to HTTP clients."""
import shutil
import subprocess
from urllib.parse import quote

from .config import CameraError


def rtsp_url(config):
    config.require_password()
    return (f"rtsp://{config.host}:554/user={quote(config.username, safe='')}"
            f"&password={quote(config.password, safe='')}&channel=1&stream=0.sdp?real_stream")


def mjpeg(config):
    from .media_runtime import media_slot
    with media_slot("live"):
        yield from _mjpeg(config)


def _mjpeg(config):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CameraError("ffmpeg를 설치해야 영상 중계를 사용할 수 있습니다.")
    command = [ffmpeg, "-hide_banner", "-loglevel", "quiet", "-nostdin",
        "-rtsp_transport", "tcp", "-timeout", "5000000", "-i", rtsp_url(config),
        "-an", "-threads", "2", "-filter_threads", "1", "-vf", "fps=5,scale=960:-1", "-c:v", "mjpeg", "-q:v", "5",
        "-f", "mpjpeg", "-boundary_tag", "frame", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = bytearray()
    try:
        while True:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > 8 * 1024 * 1024:
                raise CameraError("실시간 영상 프레임 크기를 초과했습니다.")
            # Publish complete JPEGs so a slow subscriber may drop whole frames.
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                if end < 0:
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                       str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
    finally:
        process.stdout.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
