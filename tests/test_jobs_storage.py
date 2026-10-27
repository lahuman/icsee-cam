import threading
import time
from datetime import datetime, timezone

import pytest

from icsee_ptz.jobs import JobQueue
from icsee_ptz.config import CameraError
from icsee_ptz import timelapse as tl


def until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('condition not met before deadline')


def test_queue_dedupe_capacity_and_owner_isolation(tmp_path):
    queue = JobQueue(tmp_path)
    first = queue.submit('render', {'day': 'one'}, 'alice')
    assert queue.submit('render', {'day': 'one'}, 'alice')['id'] == first['id']
    with pytest.raises(CameraError):
        queue.submit('render', {'day': 'one'}, 'bob')
    for day in ('two', 'three'):
        queue.submit('render', {'day': day}, 'alice')
    with pytest.raises(CameraError):
        queue.submit('render', {'day': 'four'}, 'alice')
    queue.cancel(first['id'])
    assert queue.get(first['id'])['state'] == 'cancelled'
    queue.submit('render', {'day': 'four'}, 'alice')


def test_two_queue_instances_never_execute_more_than_two_jobs(tmp_path):
    active = 0
    peak = 0
    guard = threading.Lock()
    release = threading.Event()
    def executor(kind, payload, cancel):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(active, peak)
        try:
            while not release.wait(.01) and not cancel.is_set():
                pass
            return {'ok': True}
        finally:
            with guard:
                active -= 1
    first = JobQueue(tmp_path, executor)
    second = JobQueue(tmp_path, executor)
    jobs = [first.submit('render', {'n': n}, str(n)) for n in range(5)]
    first.start(); second.start()
    try:
        until(lambda: active == 2)
        assert first.summary()['queued'] == 3
        first.cancel(jobs[0]['id'])
        until(lambda: first.get(jobs[0]['id'])['state'] == 'cancelled')
        release.set()
        until(lambda: first.summary()['queued'] == first.summary()['running'] == 0)
        assert peak == 2
    finally:
        release.set(); first.close(); second.close()


def test_crashed_job_is_marked_interrupted_without_replay(tmp_path):
    calls = []
    queue = JobQueue(tmp_path, lambda *args: calls.append(args))
    job = queue.submit('sd', {'n': 1})
    with queue.db() as db:
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (job['id'],))
    queue.start()
    try:
        until(lambda: queue.get(job['id'])['state'] == 'failed')
        assert queue.get(job['id'])['result']['error']['kind'] == 'interrupted'
        assert not calls
    finally:
        queue.close()


def test_storage_totals_follow_insert_update_replace_delete_without_tree_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    frame = archive.save(b'12345', datetime(2026, 1, 1, tzinfo=timezone.utc))
    with archive.db() as db:
        db.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?,?)', ('2026-01-01',30,'2026-01-01/out.mp4',1,'hash',10,1,'now'))
        db.execute('INSERT OR REPLACE INTO exports VALUES (?,?,?,?,?,?,?,?)', ('2026-01-01',30,'2026-01-01/out.mp4',1,'hash2',20,1,'later'))
        db.execute('UPDATE frames SET bytes=7')
    from pathlib import Path
    monkeypatch.setattr(Path, 'rglob', lambda *args: pytest.fail('hot path must not walk files'))
    assert archive.status()['days'][0]['jpeg_bytes'] == 7
    with archive.db() as db:
        row = db.execute('SELECT frame_bytes,export_bytes FROM storage_totals').fetchone()
        assert tuple(row) == (7,20)
        db.execute('DELETE FROM frames'); db.execute('DELETE FROM exports')
    assert archive.status()['days'] == []
    with archive.db() as db:
        assert tuple(db.execute('SELECT frame_bytes,export_bytes FROM storage_totals').fetchone()) == (0,0)


def test_reconcile_preserves_unregistered_files_and_accounts_for_their_size(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    path = archive.root / 'orphan.jpg'
    path.write_bytes(b'preserve-this')
    result = archive.reconcile()
    assert result['unregistered'] == ['orphan.jpg']
    assert result['extra_bytes'] == len(b'preserve-this')
    assert path.read_bytes() == b'preserve-this'


def test_capture_queues_maintenance_instead_of_waiting_for_render(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    archive.save(b'old', datetime(2020, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(archive, 'render', lambda *args: pytest.fail('collector must not encode'))
    calls = []
    class Camera:
        def capture(self): return b'new'
        def close(self): pass
    result = archive.collect(Camera, max_frames=1, retention=True, emit=lambda _: None,
                             maintenance=lambda *args: calls.append(args))
    assert result['captured'] == 1 and len(calls) == 1


def test_recover_canonical_orphan_without_deleting_unknown_files(tmp_path, monkeypatch):
    from icsee_ptz.archive_index import recover
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    folder = archive.root / '2026-10-26' / 'frames'
    folder.mkdir(parents=True)
    photo = folder / '20261026T000000.000000Z.jpg'
    photo.write_bytes(b'\xff\xd8fixture\xff\xd9')
    unknown = folder / 'unknown.jpg'; unknown.write_bytes(b'keep')
    result = recover(archive)
    assert result['recovered'] == 1
    assert archive.frames('2026-10-26')[0]['bytes'] == photo.stat().st_size
    assert unknown.read_bytes() == b'keep'
    assert recover(archive)['recovered'] == 0


def test_full_maintenance_queue_does_not_stop_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    def busy(*args):
        raise CameraError('queue full')
    class Camera:
        def capture(self): return b'new'
        def close(self): pass
    result = archive.collect(Camera, max_frames=1, emit=lambda _: None, maintenance=busy)
    assert result['captured'] == 1


def test_active_encoding_output_is_accounted_without_full_scan(tmp_path, monkeypatch):
    from icsee_ptz.archive_index import encoding_output
    monkeypatch.setattr(tl, 'PROJECT', tmp_path)
    archive = tl.Archive(tmp_path / 'archive', '192.168.1.168')
    output = archive.root / 'temporary.mp4'
    with encoding_output(archive, output):
        before = archive.storage_bytes()
        output.write_bytes(b'x' * 100000)
        assert archive.storage_bytes() == before + 100000
        with archive.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('storage_limits',?)", ('{"max_storage_mb":0.01,"min_free_mb":1}',))
        assert not archive.encoding_space_available()
    with archive.db() as db:
        assert db.execute('SELECT count(*) FROM encoding_outputs').fetchone()[0] == 0


def test_cancelling_media_process_terminates_and_reaps_it(monkeypatch):
    import subprocess, sys
    from icsee_ptz.processes import run_media
    original = subprocess.Popen
    children = []
    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', start)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(CameraError, match='취소'):
        run_media([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=60, cancel=cancel)
    assert len(children) == 1 and children[0].poll() is not None
