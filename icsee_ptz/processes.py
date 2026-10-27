"""Cancellable media subprocesses; never record command lines or stderr."""
import subprocess
import time
from .config import CameraError


def run_media(command, *, timeout, cancel=None, capture=False, reserve_path=None, check_space=None):
    from tempfile import TemporaryFile
    with TemporaryFile() as output:
        process = subprocess.Popen(command, stdout=output if capture else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        started = time.monotonic()
        next_check = started
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise CameraError('영상 작업이 취소됐습니다.')
                if time.monotonic() - started >= timeout:
                    raise CameraError('영상 작업 제한 시간을 초과했습니다.')
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + .25
                    if check_space is not None and not check_space():
                        raise CameraError('영상 작업의 저장 한도 또는 디스크 여유 기준에 도달했습니다.')
                    if reserve_path is not None:
                        import shutil
                        if shutil.disk_usage(reserve_path).free < 512 * 1024**2:
                            raise CameraError('영상 작업의 디스크 여유 공간이 부족합니다.')
                time.sleep(.05)
            if capture:
                output.seek(0)
                payload = output.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise CameraError('영상 검증 응답이 너무 큽니다.')
            else:
                payload = b''
            return subprocess.CompletedProcess(command, process.returncode, stdout=payload)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
