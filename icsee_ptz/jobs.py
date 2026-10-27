"""Small durable work queue shared by the web and collector processes."""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import secrets
import sqlite3
import threading
import time

from .config import CameraError, safe_error
from .paths import runtime_root
from .media_runtime import MediaBusy


class JobCancelled(CameraError):
    pass


class JobQueue:
    def __init__(self, root=None, executor=None, host=None):
        self.root = root or runtime_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'jobs.sqlite3'
        self.executor = executor
        self.host = host
        self.stop = threading.Event()
        self.threads = []
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    owner TEXT NOT NULL, dedupe TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, result TEXT,
                    cancel INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state,created);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_dedupe ON jobs(dedupe)
                    WHERE state IN ('queued','running');
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, kind, payload, owner='system'):
        if self.host is not None:
            payload = {**payload, 'host': self.host}
        encoded = json.dumps(payload, sort_keys=True)
        dedupe_payload = {key: payload.get(key) for key in ('host', 'recording_id', 'start', 'seconds')} if kind == 'sd' else payload
        dedupe = kind + ':' + json.dumps(dedupe_payload, sort_keys=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            expired = db.execute("SELECT id FROM jobs WHERE state NOT IN ('queued','running') AND updated < ?", (time.time() - 30 * 86400,)).fetchall()
            for row in expired:
                (self.root / ('job-' + row['id'] + '.lock')).unlink(missing_ok=True)
            db.execute("DELETE FROM jobs WHERE state NOT IN ('queued','running') AND updated < ?", (time.time() - 30 * 86400,))
            previous = db.execute("SELECT id,owner FROM jobs WHERE dedupe=? AND state IN ('queued','running')", (dedupe,)).fetchone()
            if previous:
                if previous['owner'] != owner:
                    raise CameraError('같은 작업이 이미 진행 중입니다.')
                return self.get(previous['id'])
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 32:
                raise MediaBusy('영상 작업 대기열이 가득 찼습니다.')
            if owner != 'system' and db.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone()[0] >= 3:
                raise MediaBusy('사용자당 최대 3개의 영상 작업을 요청할 수 있습니다.')
            token = secrets.token_hex(16)
            now = time.time()
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,'queued',?,?,NULL,0)", (token, kind, encoded, owner, dedupe, now, now))
        return self.get(token)

    def get(self, token):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (token,)).fetchone()
        if row is None:
            return None
        return {'id': row['id'], 'kind': row['kind'], 'owner': row['owner'], 'state': row['state'],
                'created_at': row['created'], 'updated_at': row['updated'],
                'result': json.loads(row['result']) if row['result'] else None}

    def cancel(self, token):
        with self.db() as db:
            db.execute("UPDATE jobs SET cancel=1, state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END, updated=? WHERE id=? AND state IN ('queued','running')", (time.time(), token))

    def summary(self):
        with self.db() as db:
            counts = {r['state']: r['n'] for r in db.execute('SELECT state,count(*) AS n FROM jobs GROUP BY state')}
        return {key: counts.get(key, 0) for key in ('queued','running','failed','cancelled','succeeded')}

    def _claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM jobs WHERE state IN ('queued','running') ORDER BY created LIMIT 32").fetchall()
            for row in rows:
                lock = (self.root / ('job-' + row['id'] + '.lock')).open('a')
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    continue
                if row['state'] == 'running':
                    # The OS released this job's lock after a crash. Do not replay
                    # a camera operation whose result may have been lost.
                    db.execute("UPDATE jobs SET state='failed', updated=?, result=? WHERE id=?",
                               (time.time(), json.dumps({'error': {'kind': 'interrupted'}}), row['id']))
                    lock.close()
                    continue
                db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), row['id']))
                return dict(row), lock
        return None

    def _worker(self):
        while not self.stop.is_set():
            slot = None
            for index in range(2):
                candidate = (self.root / f'worker-{index}.lock').open('a')
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    slot = candidate
                    break
                except BlockingIOError:
                    candidate.close()
            if slot is None:
                self.stop.wait(.2)
                continue
            try:
                claimed = self._claim()
                if claimed is None:
                    self.stop.wait(.2)
                    continue
                row, lock = claimed
                cancellation = threading.Event()
                finished = threading.Event()
                def monitor():
                    while not finished.wait(.2):
                        try:
                            with self.db() as db:
                                cancel = db.execute('SELECT cancel FROM jobs WHERE id=?', (row['id'],)).fetchone()[0]
                        except Exception:
                            cancellation.set()
                            return
                        if cancel or self.stop.is_set():
                            cancellation.set()
                            return
                watcher = threading.Thread(target=monitor, daemon=True)
                watcher.start()
                state, result = 'failed', None
                try:
                    result = self.executor(row['kind'], json.loads(row['payload']), cancellation)
                    state = 'cancelled' if cancellation.is_set() else 'succeeded'
                except MediaBusy:
                    state = 'cancelled' if cancellation.is_set() else 'queued'
                except Exception as exc:
                    state = 'cancelled' if cancellation.is_set() else 'failed'
                    result = {'error': safe_error(exc)}
                finally:
                    finished.set()
                    watcher.join()
                    try:
                        with self.db() as db:
                            # A cancel arriving just as the executor finishes wins.
                            db.execute("UPDATE jobs SET state=CASE WHEN cancel=1 THEN 'cancelled' ELSE ? END, result=?, updated=? WHERE id=?",
                                       (state, json.dumps(result), time.time(), row['id']))
                    finally:
                        lock.close()
                    from .audit import event
                    event('media_job_finished', kind=row['kind'], state=state, job_id=row['id'])
                if state == 'queued':
                    self.stop.wait(.5)
            except Exception:
                # Keep a transient SQLite/storage error from killing the worker.
                self.stop.wait(1)
            finally:
                slot.close()

    def start(self):
        if self.threads:
            return
        self.stop.clear()
        for index in range(2):
            thread = threading.Thread(target=self._worker, name=f'media-job-{index}', daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
        self.threads = [thread for thread in self.threads if thread.is_alive()]


def executor(config, archive_factory=None, sd_service=None):
    def execute(kind, payload, cancel):
        from .timelapse import Archive, KST
        if payload.get('host', config.host) != config.host:
            raise CameraError('다른 카메라의 작업은 실행할 수 없습니다. 별도 실행 디렉터리를 사용하세요.')
        if cancel.is_set():
            raise JobCancelled('작업이 취소됐습니다.')
        if kind in ('render', 'maintenance'):
            candidate = archive_factory() if archive_factory else None
            archive = candidate if candidate is not None and str(candidate.root) == payload['root'] else Archive(payload['root'], config.host)
            if kind == 'render':
                result = archive.render(payload['day'], payload.get('fps', 30), cancel=cancel)
                from urllib.parse import urlencode
                return {**result, 'url': f"/api/timelapse/{payload['day']}/video?" + urlencode({'fps': 30, 'v': result['source_hash']})}
            archive.reconcile()
            if payload.get('retention'):
                archive.prune(cancel=cancel)
            for day in archive.days():
                if cancel.is_set():
                    raise JobCancelled('작업이 취소됐습니다.')
                if day < datetime.now(KST).date().isoformat():
                    archive.render(day, cancel=cancel)
            return {'state': 'complete'}
        if kind == 'sd':
            if time.time() > payload.get('expires_at', 0):
                raise CameraError('녹화 목록의 유효 시간이 지났습니다. 다시 조회해 주세요.')
            from .sd_recordings import SDService
            from .paths import data_root
            service = sd_service or SDService(config, data_root() / 'sd-preview')
            # Record originates from the server's allowlisted camera search.
            key = payload['recording_id']
            with service.guard:
                service.records[key] = (time.monotonic(), payload['record'])
            result = service.prepare(key, payload['start'], payload['seconds'], cancel)
            return {**result, 'url': f"/api/sd/preview/{result['id']}.mp4"}
        raise CameraError('지원하지 않는 작업입니다.')
    return execute
