from datetime import datetime,timezone,timedelta
import fcntl
from pathlib import Path
import shutil
import subprocess
import pytest
from icsee_ptz.config import CameraError
from icsee_ptz import timelapse as tl

@pytest.fixture
def archive(tmp_path,monkeypatch):
    monkeypatch.setattr(tl,'PROJECT',tmp_path)
    return tl.Archive(tmp_path/'archive','192.168.1.168')

@pytest.fixture
def jpeg(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg not installed')
    path=tmp_path/'fixture.jpg'
    subprocess.run([ffmpeg,'-loglevel','quiet','-f','lavfi','-i','color=c=blue:s=64x48',
                    '-frames:v','1',str(path)],check=True,timeout=15)
    return path.read_bytes()

def test_storage_korean_day_and_exact_bytes(archive):
    at=datetime(2026,10,24,15,0,tzinfo=timezone.utc)
    frame=archive.save(b'jpeg-fixture',at)
    assert frame['day']=='2026-10-25'
    assert (archive.root/frame['path']).read_bytes()==b'jpeg-fixture'
    assert archive.status()['days'][0]['frames']==1
    with pytest.raises(CameraError):archive.save(b'other',at)
    assert (archive.root/frame['path']).read_bytes()==b'jpeg-fixture'

def test_host_cannot_be_mixed_in_one_archive(archive):
    with pytest.raises(CameraError):tl.Archive(archive.root,'192.168.1.169')

def test_storage_outside_project_refused(archive,tmp_path):
    with pytest.raises(CameraError):tl.archive_root(tmp_path.parent/'outside')

def test_another_collector_cannot_start(archive):
    with (archive.root/'.collector.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert archive.running()
        with pytest.raises(CameraError):archive.collect(lambda:pytest.fail('must not connect'))
    assert not archive.running()

class Clock:
    def __init__(self):self.now=0
    def monotonic(self):return self.now
    def event(self):
        clock=self
        class Event:
            stopped=False
            def is_set(self):return self.stopped
            def set(self):self.stopped=True
            def wait(self,seconds):clock.now+=seconds
        return Event()

def fake_clock(monkeypatch):
    c=Clock();monkeypatch.setattr(tl.time,'monotonic',c.monotonic)
    monkeypatch.setattr(tl.threading,'Event',c.event)
    return c

def test_exact_thirty_second_cadence_without_camera(archive,monkeypatch):
    clock=fake_clock(monkeypatch);starts=[]
    class Camera:
        def capture(self):starts.append(clock.now);return b'fixture'
        def close(self):pass
    result=archive.collect(Camera,interval=30,max_frames=3,emit=lambda _:None)
    assert result['captured']==3
    assert starts==[0,30,60]

def test_slow_capture_skips_slots_without_burst(archive,monkeypatch):
    clock=fake_clock(monkeypatch);starts=[];events=[]
    class Camera:
        def capture(self):starts.append(clock.now);clock.now+=35;return b'fixture'
        def close(self):pass
    archive.collect(Camera,interval=30,max_frames=3,emit=events.append)
    assert starts==[0,60,120]
    assert any(e['event']=='skipped_slots' for e in events)

def test_failed_capture_not_counted_and_secrets_not_saved(archive,monkeypatch):
    from requests import ConnectionError
    fake_clock(monkeypatch);calls=[]
    class Camera:
        def capture(self):
            calls.append(1)
            if len(calls)==1:raise ConnectionError('http://admin:secret-password@camera')
            return b'fixture'
        def close(self):pass
    archive.collect(Camera,interval=30,max_frames=1,emit=lambda _:None)
    status=archive.status()
    assert status['days'][0]['frames']==1
    assert 'secret-password' not in str(status)
    assert len(status['recent_errors'])==1


def test_outage_recovers_with_backoff_then_regular_interval(archive,monkeypatch):
    clock = fake_clock(monkeypatch)
    calls=[]; events=[]
    class Camera:
        def capture(self):
            calls.append(clock.now)
            if len(calls) <= 2: raise ConnectionError()
            return b'fixture'
        def close(self): pass
    result = archive.collect(Camera, max_frames=2, duration=100, emit=events.append)
    assert calls == [0, 2, 6, 36]
    assert result['captured'] == 2
    assert any(e['event'] == 'reconnected' for e in events)


def test_outage_does_not_extend_duration_limit(archive,monkeypatch):
    clock = fake_clock(monkeypatch)
    calls=[]
    def factory():
        calls.append(clock.now)
        raise ConnectionError()
    result = archive.collect(factory, duration=5, emit=lambda _:None)
    assert calls == [0, 2]
    assert result['reason'] == 'duration_limit'


def test_authentication_failure_stops_without_repeated_login(archive,monkeypatch):
    from icsee_ptz.config import AuthenticationError
    fake_clock(monkeypatch)
    calls=[]
    def factory():
        calls.append(1)
        raise AuthenticationError('denied')
    result = archive.collect(factory, emit=lambda _:None)
    assert len(calls) == 1
    assert result['reason'] == 'authentication_error'

def test_stop_request_prevents_next_capture(archive,monkeypatch):
    fake_clock(monkeypatch)
    class Camera:
        def capture(self):(archive.root/'.stop').touch();return b'fixture'
        def close(self):pass
    assert archive.collect(Camera,emit=lambda _:None)['captured']==1


def test_storage_limit_stops_without_connecting_or_deleting(archive):
    marker = archive.root / 'keep.txt'
    marker.write_text('preserve')
    result = archive.collect(lambda: pytest.fail('must not connect'),
                             max_storage_mb=.001, emit=lambda _:None)
    assert result['reason'] == 'storage_limit'
    assert result['captured'] == 0
    assert marker.read_text() == 'preserve'
    assert archive.status()['last_run']['state'] == 'stopped'


def test_disk_reserve_prevents_capture(archive,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tl.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100))
    result = archive.collect(lambda: pytest.fail('must not connect'), emit=lambda _:None)
    assert result['reason'] == 'storage_limit'


def test_incoming_frame_exceeding_quota_is_not_saved(archive):
    closed=[]
    class Camera:
        def capture(self): return b'x' * (1024**2)
        def close(self): closed.append(True)
    result = archive.collect(Camera, max_storage_mb=1, emit=lambda _:None)
    assert result['reason'] == 'storage_limit'
    assert result['captured'] == 0
    assert archive.days() == []
    assert closed == [True]

def test_restart_and_rollover_exports_closed_korean_day(archive,monkeypatch):
    fake_clock(monkeypatch)
    archive.save(b'fixture',datetime(2020,1,1,tzinfo=timezone.utc))
    rendered=[]
    monkeypatch.setattr(archive,'render',lambda day:rendered.append(day) or {'state':'test'})
    class Camera:
        def capture(self):return b'fixture'
        def close(self):pass
    archive.collect(Camera,max_frames=1,emit=lambda _:None)
    assert rendered==['2020-01-01']

def test_real_ffmpeg_export_frame_count_and_cache(archive,jpeg):
    at=datetime(2026,10,25,tzinfo=timezone.utc)
    for index in range(5):archive.save(jpeg,at+timedelta(seconds=30*index))
    result=archive.render('2026-10-25',fps=30)
    assert result['state']=='verified'
    assert result['frame_count']==5
    assert result['duration']==pytest.approx(5/30,abs=.01)
    assert (archive.root/result['path']).is_file()
    assert len(archive.frames('2026-10-25'))==5
    assert archive.render('2026-10-25',fps=30)['state']=='already_exported'

def test_corrupt_source_never_publishes_video(archive,jpeg):
    frame=archive.save(jpeg,datetime(2026,10,25,tzinfo=timezone.utc))
    (archive.root/frame['path']).write_bytes(b'corrupt')
    with pytest.raises(CameraError):archive.render('2026-10-25')
    assert not (archive.root/'2026-10-25/timelapse-30fps.mp4').exists()

def test_invalid_date_cannot_escape_archive(archive):
    with pytest.raises(CameraError):archive.render('../../elsewhere')


def test_retention_calendar_boundaries_and_mp4_survives_photos(archive,jpeg):
    now=datetime(2026,10,25,12,tzinfo=tl.KST)
    for day in ['2026-10-25','2026-10-23','2026-10-22','2026-09-26','2026-09-25']:
        archive.save(jpeg,datetime.fromisoformat(day).replace(tzinfo=tl.KST))
    result=archive.prune(now)
    assert result['errors']==[]
    assert result['photos_deleted']==3
    assert archive.days()==['2026-10-23','2026-10-25']
    status=archive.status()
    assert [e['day'] for e in status['exports']]==['2026-09-26','2026-10-22']
    assert next(d for d in status['days'] if d['day']=='2026-10-22')['photos_retained'] is False
    assert next(d for d in status['days'] if d['day']=='2026-10-22')['jpeg_bytes']==0
    assert archive.prune(now)['photos_deleted']==0
    later=archive.prune(datetime(2026,10,26,12,tzinfo=tl.KST))
    assert later['videos_deleted']==1
    assert not (archive.root/'2026-09-26/timelapse-30fps.mp4').exists()


def test_failed_export_preserves_expiring_photos(archive,monkeypatch):
    frame=archive.save(b'original',datetime(2026,10,21,tzinfo=tl.KST))
    def fail(day):raise CameraError('encode failed')
    monkeypatch.setattr(archive,'render',fail)
    result=archive.prune(datetime(2026,10,25,tzinfo=tl.KST))
    assert result['errors'] and result['photos_deleted']==0
    assert (archive.root/frame['path']).read_bytes()==b'original'
    assert len(archive.frames('2026-10-21'))==1


def test_legacy_slow_video_is_removed_after_daily_summary_is_created(archive,jpeg):
    archive.save(jpeg,datetime(2026,10,25,tzinfo=tl.KST))
    legacy=archive.root/'2026-10-25/timelapse-2fps.mp4'
    legacy.write_bytes(b'old')
    with archive.db() as db:
        db.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?,?)',
            ('2026-10-25',2,'2026-10-25/timelapse-2fps.mp4',1,'old',3,.5,'now'))
    result=archive.prune(datetime(2026,10,25,12,tzinfo=tl.KST))
    assert result['errors']==[] and result['videos_deleted']==1
    assert not legacy.exists()
    assert (legacy.parent/'timelapse-30fps.mp4').is_file()
    with pytest.raises(CameraError):archive.render('2026-10-25',2)


def test_retention_does_not_follow_symlinked_day_directory(archive,tmp_path):
    frame=archive.save(b'original',datetime(2026,10,21,tzinfo=tl.KST))
    folder=archive.root/'2026-10-21'
    outside=tmp_path/'outside';folder.rename(outside);folder.symlink_to(outside,target_is_directory=True)
    result=archive.prune(datetime(2026,10,25,tzinfo=tl.KST))
    assert result['errors'] and result['photos_deleted']==0
    assert (outside/'frames'/Path(frame['path']).name).read_bytes()==b'original'
