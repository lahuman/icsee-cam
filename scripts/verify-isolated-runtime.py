"""Opt-in Docker integration checks. Mount this script only; never mount production data.

Run the deployed image with --network none, a writable /tmp, and
ICSEE_VALIDATION_ISOLATED=1. All accounts, media and databases are synthetic.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

assert os.environ.get('ICSEE_VALIDATION_ISOLATED') == '1', 'isolated container only'
ROOT = Path('/tmp/icsee-validation')
os.environ['ICSEE_DATA_DIR'] = str(ROOT / 'data')
os.environ['ICSEE_RUNTIME_DIR'] = str(ROOT / 'runtime')
ROOT.mkdir(parents=True, exist_ok=True)
from icsee_ptz.config import Config
from icsee_ptz.jobs import JobQueue, executor
from icsee_ptz.timelapse import Archive, KST

CONFIG = Config(password='synthetic-camera-unused')

def until(predicate, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('condition timed out')


def jpeg(name, size):
    path = ROOT / name
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=' + size, '-frames:v', '1', '-threads', '2', str(path)],
                   check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path.read_bytes()


def serve_expiry():
    import uvicorn
    import icsee_ptz.web as web
    import icsee_ptz.web_auth as auth_module
    from unittest.mock import Mock
    auth_module.SESSION_SECONDS = 4  # This disposable process only; production remains 8h.
    web.verified_profile = lambda config: {'validation': True}
    image = (ROOT / 'small.jpg').read_bytes()
    def video():
        while True:
            time.sleep(.03)
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + image + b'\r\n'
    async def audio():
        while True:
            await asyncio.sleep(.03)
            yield b'\x00\x00' * 160
    app = web.create_app(CONFIG, manager=Mock(), sd_service=Mock(), port=8123,
                         web_auth=auth_module.WebAuth('validation', 'synthetic-login'),
                         video_factory=video, listen_factory=audio)
    uvicorn.run(app, host='127.0.0.1', port=8123, access_log=False, log_level='critical')


def crash_worker():
    def block(kind, payload, cancel):
        (ROOT / 'crash-started').write_text('started')
        while not cancel.wait(.05):
            pass
    queue = JobQueue(ROOT / 'crash-queue', block)
    # One worker makes the subsequent queued render remain queued at the crash.
    worker = threading.Thread(target=queue._worker)
    worker.start()
    worker.join()


def expiry_check():
    import websockets
    server = subprocess.Popen([sys.executable, __file__, 'expiry-server'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def health():
        try:
            return opener.open('http://127.0.0.1:8123/healthz', timeout=.5).status == 200
        except Exception:
            return False
    conn = None
    try:
        until(health)
        body = json.dumps({'username': 'validation', 'password': 'synthetic-login'}).encode()
        req = urllib.request.Request('http://127.0.0.1:8123/api/login', data=body,
                                     headers={'X-ICSEE-Control': 'local', 'Content-Type': 'application/json'})
        with opener.open(req) as response:
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        started = time.monotonic()
        conn = http.client.HTTPConnection('127.0.0.1', 8123, timeout=8)
        conn.request('GET', '/video', headers={'Cookie': cookie})
        response = conn.getresponse()
        assert response.status == 200
        got_video = threading.Event()
        video_closed = threading.Event()
        chunks = []
        def receive_video():
            try:
                while response.read(512):
                    chunks.append(1)
                    got_video.set()
            except http.client.IncompleteRead:
                pass  # Revoked HTTP streams are intentionally truncated.
            finally:
                video_closed.set()
        reader = threading.Thread(target=receive_video)
        reader.start()
        async def receive_audio():
            blocks = 0
            async with websockets.connect('ws://127.0.0.1:8123/audio/listen', proxy=None,
                    origin='http://127.0.0.1:8123', additional_headers={'Cookie': cookie}) as ws:
                try:
                    async for value in ws:
                        if isinstance(value, bytes):
                            blocks += 1
                except websockets.exceptions.ConnectionClosed as exc:
                    assert exc.rcvd and exc.rcvd.code == 1008
            assert blocks > 0
            return blocks
        blocks = asyncio.run(_timeout(receive_audio(), 8))
        assert got_video.is_set() and video_closed.wait(3)
        reader.join(3)
        elapsed = time.monotonic() - started
        assert 3 <= elapsed < 8, elapsed
        req = urllib.request.Request('http://127.0.0.1:8123/api/me', headers={'Cookie': cookie})
        try:
            opener.open(req)
            raise AssertionError('expired session accepted')
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        return {'seconds': round(elapsed, 2), 'video_chunks': len(chunks),
                'audio_blocks': blocks, 'websocket_close': 1008, 'expired_http_status': 401}
    finally:
        if conn:
            conn.close()
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait()


async def _timeout(coro, seconds):
    return await asyncio.wait_for(coro, seconds)


def main():
    small = jpeg('small.jpg', '64x48')
    large = jpeg('large.jpg', '1280x720')
    report = {'expiry': expiry_check()}
    print(json.dumps({'phase': 'expiry_passed', **report['expiry']}), flush=True)
    now = datetime.now(KST).replace(hour=12, minute=0, second=0, microsecond=0)
    archive = Archive(ROOT / 'data/retention', CONFIG.host)
    for days_ago in [31, 3, 1, 0]:
        for n in range(3):
            archive.save(small, now - timedelta(days=days_ago) + timedelta(seconds=n))
    old = (now - timedelta(days=31)).date().isoformat()
    photo_old = (now - timedelta(days=3)).date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    today = now.date().isoformat()
    archive.render(old)
    unknown = archive.root / 'unregistered.txt'; unknown.write_text('preserve')
    queue = JobQueue(ROOT / 'pipeline-queue', executor(CONFIG))
    job = queue.submit('maintenance', {'root': str(archive.root), 'retention': True})
    queue.start()
    try:
        until(lambda: queue.get(job['id'])['state'] in ['succeeded', 'failed'])
        assert queue.get(job['id'])['state'] == 'succeeded'
        assert not archive.frames(old) and not (archive.root / old / 'timelapse-30fps.mp4').exists()
        assert not archive.frames(photo_old) and (archive.root / photo_old / 'timelapse-30fps.mp4').exists()
        assert len(archive.frames(yesterday)) == len(archive.frames(today)) == 3
        assert (archive.root / yesterday / 'timelapse-30fps.mp4').exists()
        assert not (archive.root / today / 'timelapse-30fps.mp4').exists()
        assert unknown.read_text() == 'preserve'
        with archive.db() as db:
            assert tuple(db.execute('SELECT frame_bytes,export_bytes FROM storage_totals').fetchone()) == (
                db.execute('SELECT sum(bytes) FROM frames').fetchone()[0],
                db.execute('SELECT sum(bytes) FROM exports').fetchone()[0])
        report['retention'] = {'expired_photos_deleted': 6, 'expired_videos_deleted': 1,
                               'summary_preserved': True, 'yesterday_auto_encoded': True,
                               'unknown_file_preserved': True, 'aggregates_match': True}
        bad = Archive(ROOT / 'data/corrupt', CONFIG.host)
        saved = bad.save(b'corrupt-not-jpeg', now - timedelta(days=4))
        result = bad.prune(now=now)
        assert result['errors'] and result['photos_deleted'] == 0 and (bad.root / saved['path']).exists()
        report['corrupt_source_preserved'] = True

        # Exercise the collector across KST midnight without changing the system clock.
        import icsee_ptz.timelapse as tl
        real_datetime = tl.datetime
        moment = [now.replace(hour=0) - timedelta(seconds=2)]
        class ValidationDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment[0].astimezone(tz) if tz else moment[0].replace(tzinfo=None)
        rollover = Archive(ROOT / 'data/rollover', CONFIG.host)
        rollover.save(small, moment[0] - timedelta(seconds=1))
        maintenance_days = []
        class Camera:
            def capture(self):
                moment[0] += timedelta(seconds=1)
                return small
            def close(self):
                pass
        def maintain(current, retention):
            maintenance_days.append(moment[0].date().isoformat())
            queue.submit('maintenance', {'root': str(current.root), 'retention': retention,
                                        'day': moment[0].date().isoformat()})
        tl.datetime = ValidationDateTime
        try:
            result = rollover.collect(Camera, interval=1, max_frames=3, emit=lambda _: None, maintenance=maintain)
        finally:
            tl.datetime = real_datetime
        until(lambda: queue.summary()['queued'] == queue.summary()['running'] == 0)
        assert result['captured'] == 3 and maintenance_days == [yesterday, today]
        with rollover.db() as db:
            assert db.execute('SELECT frame_count FROM exports WHERE day=?', (yesterday,)).fetchone()[0] == 2
        report['kst_rollover'] = {'captured': 3, 'maintenance_days': maintenance_days, 'previous_day_export_frames': 2}

        # A real, deliberately long encode runs alongside a one-second synthetic collector.
        active = Archive(ROOT / 'data/cancellation', CONFIG.host)
        for n in range(800):
            active.save(large, now - timedelta(days=1) + timedelta(milliseconds=n))
        # Pace only this synthetic FFmpeg input so capture/encoding overlap is deterministic.
        # The production encoder, subprocess cancellation and output checks remain in use.
        import icsee_ptz.processes as media_processes
        original_run_media = media_processes.run_media
        def paced_media(command, **kwargs):
            if '-framerate' in command:
                command = list(command)
                command.insert(command.index('-framerate'), '-re')
            return original_run_media(command, **kwargs)
        media_processes.run_media = paced_media
        job = queue.submit('render', {'root': str(active.root), 'day': yesterday})
        def encoding():
            with active.db() as db:
                return db.execute('SELECT count(*) FROM encoding_outputs').fetchone()[0] > 0
        until(encoding)
        captures_during_render = []
        class NewCamera:
            def capture(self):
                captures_during_render.append(queue.get(job['id'])['state'] == 'running')
                return small
            def close(self):
                pass
        result = active.collect(NewCamera, interval=1, max_frames=3, emit=lambda _: None,
                                maintenance=lambda *args: None)
        assert result['captured'] == 3 and all(captures_during_render), {'capture': result, 'overlap': captures_during_render, 'job': queue.get(job['id'])}
        start = time.monotonic(); queue.cancel(job['id'])
        until(lambda: queue.get(job['id'])['state'] == 'cancelled', 10)
        assert not (active.root / yesterday / 'timelapse-30fps.mp4').exists()
        assert not list(active.root.glob('*/.encode-*'))
        assert len(active.frames(yesterday)) == 800
        with active.db() as db:
            assert db.execute('SELECT count(*) FROM encoding_outputs').fetchone()[0] == 0
        ffmpeg_count = sum(p.read_text().strip() == 'ffmpeg' for p in Path('/proc').glob('[0-9]*/comm'))
        assert ffmpeg_count == 0
        media_processes.run_media = original_run_media
        report['cancellation'] = {'seconds': round(time.monotonic() - start, 2),
                                  'captures_during_encoding': 3, 'source_frames_preserved': 800,
                                  'temporary_outputs_remaining': 0, 'ffmpeg_remaining': 0}
    finally:
        queue.close()
    print(json.dumps({'phase': 'storage_passed', **report}), flush=True)

    # Crash a real worker process; a queued real render must survive and run on restart.
    restart_archive = Archive(ROOT / 'data/restart', CONFIG.host)
    restart_archive.save(small, now)
    crash = JobQueue(ROOT / 'crash-queue')
    interrupted = crash.submit('crash-probe', {})
    pending = crash.submit('render', {'root': str(restart_archive.root), 'day': today})
    worker = subprocess.Popen([sys.executable, __file__, 'crash-worker'])
    try:
        until(lambda: (ROOT / 'crash-started').exists())
        assert crash.get(interrupted['id'])['state'] == 'running'
        assert crash.get(pending['id'])['state'] == 'queued'
    finally:
        worker.kill(); worker.wait()
    replayed = []
    actual = executor(CONFIG)
    def guarded(kind, payload, cancel):
        if kind == 'crash-probe':
            replayed.append(True)
            raise AssertionError('interrupted operation replayed')
        return actual(kind, payload, cancel)
    restarted = JobQueue(ROOT / 'crash-queue', guarded)
    restarted.start()
    try:
        until(lambda: restarted.get(pending['id'])['state'] == 'succeeded')
        result = restarted.get(interrupted['id'])
        assert result['state'] == 'failed' and result['result']['error']['kind'] == 'interrupted'
        assert not replayed
        report['restart'] = {'interrupted_job': 'failed', 'replayed': False, 'queued_render': 'succeeded'}
    finally:
        restarted.close()
    report['passed'] = True
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'expiry-server':
        serve_expiry()
    elif len(sys.argv) > 1 and sys.argv[1] == 'crash-worker':
        crash_worker()
    else:
        main()
