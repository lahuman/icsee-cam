"""Transactional daily aggregates and bounded storage accounting."""
from pathlib import Path
import time


def install(db):
    db.executescript('''
        CREATE INDEX IF NOT EXISTS frames_day_capture ON frames(day,captured_at,id);
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT PRIMARY KEY, frames INTEGER NOT NULL, jpeg_bytes INTEGER NOT NULL,
            first_capture TEXT, last_capture TEXT);
        CREATE TABLE IF NOT EXISTS encoding_outputs (path TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS storage_totals (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), frame_bytes INTEGER NOT NULL,
            export_bytes INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS frames_add AFTER INSERT ON frames BEGIN
            INSERT INTO daily_stats VALUES (NEW.day,1,NEW.bytes,NEW.captured_at,NEW.captured_at)
            ON CONFLICT(day) DO UPDATE SET frames=frames+1,jpeg_bytes=jpeg_bytes+NEW.bytes,
                first_capture=min(first_capture,NEW.captured_at),last_capture=max(last_capture,NEW.captured_at);
            UPDATE storage_totals SET frame_bytes=frame_bytes+NEW.bytes WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS frames_remove AFTER DELETE ON frames BEGIN
            UPDATE storage_totals SET frame_bytes=frame_bytes-OLD.bytes WHERE singleton=1;
            UPDATE daily_stats SET frames=frames-1,jpeg_bytes=jpeg_bytes-OLD.bytes,
                first_capture=(SELECT min(captured_at) FROM frames WHERE day=OLD.day),
                last_capture=(SELECT max(captured_at) FROM frames WHERE day=OLD.day) WHERE day=OLD.day;
            DELETE FROM daily_stats WHERE frames=0;
        END;
        CREATE TRIGGER IF NOT EXISTS frames_change AFTER UPDATE ON frames BEGIN
            UPDATE storage_totals SET frame_bytes=frame_bytes+NEW.bytes-OLD.bytes WHERE singleton=1;
            DELETE FROM daily_stats WHERE day IN (OLD.day,NEW.day);
            INSERT INTO daily_stats SELECT day,count(*),sum(bytes),min(captured_at),max(captured_at)
                FROM frames WHERE day IN (OLD.day,NEW.day) GROUP BY day;
        END;
        CREATE TRIGGER IF NOT EXISTS exports_add AFTER INSERT ON exports BEGIN
            UPDATE storage_totals SET export_bytes=export_bytes+NEW.bytes WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS exports_remove AFTER DELETE ON exports BEGIN
            UPDATE storage_totals SET export_bytes=export_bytes-OLD.bytes WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS exports_change AFTER UPDATE ON exports BEGIN
            UPDATE storage_totals SET export_bytes=export_bytes+NEW.bytes-OLD.bytes WHERE singleton=1;
        END;
    ''')
    if db.execute('SELECT 1 FROM storage_totals WHERE singleton=1').fetchone() is None:
        db.execute('INSERT OR REPLACE INTO daily_stats SELECT day,count(*),sum(bytes),min(captured_at),max(captured_at) FROM frames GROUP BY day')
        db.execute('INSERT OR IGNORE INTO storage_totals SELECT 1,coalesce((SELECT sum(bytes) FROM frames),0),coalesce((SELECT sum(bytes) FROM exports),0)')


def storage_bytes(archive):
    with archive.db() as db:
        row = db.execute('SELECT frame_bytes+export_bytes FROM storage_totals WHERE singleton=1').fetchone()
        extra = db.execute("SELECT value FROM settings WHERE key='unregistered_bytes'").fetchone()
        temporary = [r[0] for r in db.execute('SELECT path FROM encoding_outputs')]
    total = row[0] + (int(extra[0]) if extra else 0)
    for relative in temporary:
        path = archive.root / relative
        if archive.root in path.resolve().parents and not path.is_symlink():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    for suffix in ('', '-wal', '-shm', '-journal'):
        path = archive.root / ('index.sqlite3' + suffix)
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            pass
    return total


def reconcile(archive):
    """Inventory discrepancies without deleting or trusting unregistered footage."""
    with archive.db() as db:
        registered = {r['path']: r['bytes'] for r in db.execute('SELECT path,bytes FROM frames UNION ALL SELECT path,bytes FROM exports')}
        temporary = {r[0] for r in db.execute('SELECT path FROM encoding_outputs')}
    actual = {}
    for path in archive.root.rglob('*'):
        if path.is_file() and not path.is_symlink() and archive.root in path.resolve().parents:
            if path.name not in ('index.sqlite3', 'index.sqlite3-wal', 'index.sqlite3-shm', 'index.sqlite3-journal') and not path.name.endswith('.lock'):
                try:
                    actual[path.relative_to(archive.root).as_posix()] = path.stat().st_size
                except FileNotFoundError:
                    pass
    missing = [name for name in registered if name not in actual]
    unregistered = [name for name in actual if name not in registered and name not in temporary]
    # Include size drift conservatively; never silently bless changed originals.
    extra = sum(actual[name] for name in unregistered) + sum(max(0, size-registered[name]) for name,size in actual.items() if name in registered)
    with archive.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM daily_stats')
        db.execute('INSERT INTO daily_stats SELECT day,count(*),sum(bytes),min(captured_at),max(captured_at) FROM frames GROUP BY day')
        db.execute('UPDATE storage_totals SET frame_bytes=coalesce((SELECT sum(bytes) FROM frames),0),export_bytes=coalesce((SELECT sum(bytes) FROM exports),0) WHERE singleton=1')
        db.execute("INSERT OR REPLACE INTO settings VALUES ('unregistered_bytes',?)", (str(extra),))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('last_reconciled',?)", (str(time.time()),))
    return {'missing_count': len(missing), 'unregistered_count': len(unregistered),
            'missing': missing[:100], 'unregistered': unregistered[:100], 'extra_bytes': extra}


def recover(archive):
    """Recover only canonical orphan captures while the collector is stopped."""
    import fcntl
    import hashlib
    import re
    from datetime import datetime, timezone
    from .config import CameraError
    recovered = 0
    with (archive.root / '.collector.lock').open('a') as collector:
        try:
            fcntl.flock(collector, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CameraError('복구 전 촬영기를 중지하세요.') from None
        report = reconcile(archive)
        # Limit work per invocation; preserve and report everything else.
        for relative in report['unregistered']:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}/frames/\d{8}T\d{6}\.\d{6}Z\.jpg', relative):
                continue
            path = archive.root / relative
            if path.resolve() != path.absolute() or path.stat().st_size > 8 * 1024**2:
                continue
            try:
                captured = datetime.strptime(path.stem, '%Y%m%dT%H%M%S.%fZ').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            from .timelapse import KST
            day = captured.astimezone(KST).date().isoformat()
            if day != path.parent.parent.name:
                continue
            with (path.parent.parent / '.render.lock').open('a') as render:
                try:
                    fcntl.flock(render, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                data = path.read_bytes()
                if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                    continue
                with archive.db() as db:
                    result = db.execute('INSERT OR IGNORE INTO frames(captured_at,day,path,bytes,sha256) VALUES (?,?,?,?,?)',
                                        (captured.isoformat(), day, relative, len(data), hashlib.sha256(data).hexdigest()))
                    recovered += result.rowcount
        return {'recovered': recovered, **reconcile(archive)}


from contextlib import contextmanager


@contextmanager
def encoding_output(archive, path):
    relative = str(path.relative_to(archive.root))
    with archive.db() as db:
        db.execute('INSERT INTO encoding_outputs VALUES (?)', (relative,))
    try:
        yield
    finally:
        with archive.db() as db:
            db.execute('DELETE FROM encoding_outputs WHERE path=?', (relative,))
