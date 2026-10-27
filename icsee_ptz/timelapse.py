"""30-second snapshots, recoverable local storage, verified daily H.264 export."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

from .config import CameraError, safe_error
from .reconnect import Backoff, retryable

KST = ZoneInfo("Asia/Seoul")
from .paths import project_root, data_root
PROJECT = project_root()


def archive_root(value=None):
    root = Path(value or data_root() / "timelapse").resolve()
    if PROJECT not in root.parents and data_root() not in root.parents:
        raise CameraError("타임랩스 저장소는 프로젝트 내부 경로여야 합니다.")
    return root


class Archive:
    def __init__(self, root, host):
        self.root = archive_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS frames (
                    id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, day TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exports (
                    day TEXT NOT NULL, fps INTEGER NOT NULL, path TEXT NOT NULL,
                    frame_count INTEGER NOT NULL, source_hash TEXT NOT NULL, bytes INTEGER NOT NULL,
                    duration REAL NOT NULL, verified_at TEXT NOT NULL, PRIMARY KEY(day,fps));
                CREATE TABLE IF NOT EXISTS errors (
                    at TEXT NOT NULL, detail TEXT NOT NULL);
            ''')
            from .archive_index import install
            install(db)
            existing = db.execute("SELECT value FROM settings WHERE key='host'").fetchone()
            if existing and existing[0] != host:
                raise CameraError("이 저장소는 다른 카메라에 연결돼 있습니다. 별도 저장소를 지정하세요.")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('host',?)", (host,))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / "index.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA recursive_triggers=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, data, captured_at):
        day = captured_at.astimezone(KST).date().isoformat()
        stamp = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        relative = Path(day) / "frames" / f"{stamp}.jpg"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as temp:
            temporary = Path(temp.name)
            temp.write(data)
            temp.flush()
            import os
            os.fsync(temp.fileno())
        try:
            # Unique timestamps avoid overwriting any previous capture.
            if target.exists():
                raise CameraError("같은 시각의 사진이 이미 존재합니다.")
            temporary.replace(target)
            with self.db() as db:
                db.execute("INSERT INTO frames(captured_at,day,path,bytes,sha256) VALUES (?,?,?,?,?)",
                    (captured_at.isoformat(), day, relative.as_posix(), len(data), hashlib.sha256(data).hexdigest()))
        finally:
            temporary.unlink(missing_ok=True)
        return {"day": day, "captured_at": captured_at.isoformat(),
                "path": relative.as_posix(), "bytes": len(data)}

    def record_error(self, exc):
        detail = safe_error(exc)
        with self.db() as db:
            db.execute("INSERT INTO errors VALUES (?,?)",
                       (datetime.now(timezone.utc).isoformat(), json.dumps(detail, ensure_ascii=False)))
        return detail

    def days(self):
        with self.db() as db:
            return [r[0] for r in db.execute("SELECT DISTINCT day FROM frames ORDER BY day")]

    def frames(self, day):
        with self.db() as db:
            return list(db.execute("SELECT * FROM frames WHERE day=? ORDER BY captured_at,id", (day,)))

    def running(self):
        with (self.root / ".collector.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def status(self):
        with self.db() as db:
            days = [dict(r) for r in db.execute("SELECT * FROM daily_stats ORDER BY day")]
            exports = [dict(r) for r in db.execute("SELECT * FROM exports ORDER BY day,fps")]
            errors = [dict(r) for r in db.execute("SELECT * FROM errors ORDER BY rowid DESC LIMIT 5")]
            run = db.execute("SELECT value FROM settings WHERE key='last_run'").fetchone()
        by_day = {row['day']: row for row in days}
        for row in days:
            row['photos_retained'] = True
        for export in exports:
            if export['fps'] == 30 and export['day'] not in by_day:
                by_day[export['day']] = {'day': export['day'], 'frames': export['frame_count'],
                    'jpeg_bytes': 0, 'first_capture': None, 'last_capture': None, 'photos_retained': False}
        days = sorted(by_day.values(), key=lambda row: row['day'])
        return {"running": self.running(), "timezone": "Asia/Seoul", "root": str(self.root),
                "days": days, "exports": exports, "recent_errors": errors,
                "storage_bytes": self.storage_bytes(), "disk_free_bytes": shutil.disk_usage(self.root).free,
                "last_run": json.loads(run[0]) if run else None}

    def storage_bytes(self):
        from .archive_index import storage_bytes
        return storage_bytes(self)

    def encoding_space_available(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='storage_limits'").fetchone()
        limits = json.loads(row[0]) if row else {'max_storage_mb': 1024, 'min_free_mb': 512}
        return (self.storage_bytes() <= limits['max_storage_mb'] * 1024**2
                and shutil.disk_usage(self.root).free >= limits['min_free_mb'] * 1024**2)

    def set_run(self, state):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('last_run',?)", (json.dumps(state),))

    def reconcile(self):
        from .archive_index import reconcile
        return reconcile(self)

    def render(self, day, fps=30, *, cancel=None):
        from .media_runtime import media_slot
        with media_slot('batch'):
            return self._render(day, fps, cancel=cancel)

    def _render(self, day, fps=30, *, cancel=None):
        try:
            if datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day:
                raise ValueError()
        except ValueError:
            raise CameraError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from None
        if fps != 30:
            raise CameraError("하루 요약은 30fps만 지원합니다.")
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise CameraError("타임랩스 생성에는 ffmpeg와 ffprobe가 필요합니다.")
        rows = self.frames(day)
        if not rows:
            raise CameraError("선택한 날짜에 저장된 사진이 없습니다.")
        source_hash = hashlib.sha256("".join(r["sha256"] + r["path"] for r in rows).encode()).hexdigest()
        folder = self.root / day
        with (folder / ".render.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CameraError("해당 날짜의 영상이 이미 생성 중입니다.") from None
            output = folder / f"timelapse-{fps}fps.mp4"
            with self.db() as db:
                previous = db.execute("SELECT * FROM exports WHERE day=? AND fps=?", (day,fps)).fetchone()
            if previous and previous["source_hash"] == source_hash and output.is_file():
                return {"state": "already_exported", **dict(previous)}
            # Hardlinks avoid duplicating JPEG bytes and avoid unsafe ffconcat path quoting.
            with tempfile.TemporaryDirectory(dir=folder, prefix=".encode-") as workspace:
                workspace = Path(workspace)
                for index, row in enumerate(rows):
                    if cancel is not None and cancel.is_set():
                        raise CameraError("영상 작업이 취소됐습니다.")
                    path = (self.root / row["path"]).resolve()
                    if self.root not in path.parents or not path.is_file():
                        raise CameraError("원본 사진이 없거나 저장소 밖을 가리킵니다.")
                    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                        raise CameraError("원본 사진의 무결성 검사가 실패했습니다.")
                    (workspace / f"{index:06d}.jpg").hardlink_to(path)
                temporary = workspace / "output.mp4"
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-framerate", str(fps), "-i", str(workspace / "%06d.jpg"),
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264",
                    "-preset", "medium", "-threads", "2", "-filter_threads", "1", "-crf", "28", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-an", str(temporary)]
                from .processes import run_media
                from .archive_index import encoding_output
                with encoding_output(self, temporary):
                    encoded = run_media(command, timeout=max(60, len(rows) * 2), cancel=cancel, reserve_path=self.root, check_space=self.encoding_space_available)
                    if encoded.returncode:
                        raise CameraError("타임랩스 인코딩에 실패했습니다. 원본 사진은 유지합니다.")
                    probe = run_media([ffprobe, "-v", "quiet", "-count_frames", "-show_streams",
                        "-of", "json", str(temporary)], capture=True, timeout=60, cancel=cancel)
                    if probe.returncode:
                        raise CameraError("완성 영상 검증에 실패했습니다. 원본 사진은 유지합니다.")
                    streams = json.loads(probe.stdout).get("streams", [])
                    video = next((s for s in streams if s.get("codec_type") == "video"), {})
                    if video.get("codec_name") != "h264" or int(video.get("nb_read_frames", -1)) != len(rows):
                        raise CameraError("출력 코덱/프레임 수가 예상과 다릅니다. 원본을 유지합니다.")
                    duration = float(video.get("duration", 0))
                    if duration <= 0 or abs(duration - len(rows) / fps) > 1 / fps + 0.001:
                        raise CameraError("출력 영상 길이가 예상과 다릅니다.")
                    if cancel is not None and cancel.is_set():
                        raise CameraError("영상 작업이 취소됐습니다.")
                    if not self.encoding_space_available():
                        raise CameraError("영상 저장 한도에 도달했습니다.")
                    temporary.replace(output)  # Publish only after frame-count/codec/duration verification.
            result = {"day": day, "fps": fps, "path": str(output.relative_to(self.root)),
                      "frame_count": len(rows), "source_hash": source_hash, "bytes": output.stat().st_size,
                      "duration": duration, "verified_at": datetime.now(timezone.utc).isoformat()}
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO exports VALUES (?,?,?,?,?,?,?,?)",
                    tuple(result[k] for k in ("day","fps","path","frame_count","source_hash","bytes",
                                              "duration","verified_at")))
            return {"state": "verified", **result}

    def retention_policy(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='retention_policy'").fetchone()
        return json.loads(row[0]) if row else {'photo_days': 3, 'video_days': 30}

    def set_retention_policy(self, photo_days, video_days):
        if type(photo_days) is not int or type(video_days) is not int or not 1 <= photo_days <= video_days <= 365:
            raise CameraError('보관 기간은 1~365일이며 영상 보관 기간은 사진 이상이어야 합니다.')
        policy = {'photo_days': photo_days, 'video_days': video_days}
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('retention_policy',?)", (json.dumps(policy),))
        return policy

    def prune(self, now=None, *, cancel=None):
        """Keep today + 2 photo days and today + 29 video days (KST).

        Preserve photos if a current, verified daily summary cannot be produced.
        Operate only on registered, confined paths under per-day render locks.
        """
        today = (now or datetime.now(timezone.utc)).astimezone(KST).date()
        policy = self.retention_policy()
        photo_cutoff = (today - timedelta(days=policy['photo_days'] - 1)).isoformat()
        video_cutoff = (today - timedelta(days=policy['video_days'] - 1)).isoformat()
        result = {**policy, 'photos_deleted': 0,
                  'videos_deleted': 0, 'errors': []}

        def checked(relative, day, photo=False):
            path = self.root / relative
            expected = Path(day) / 'frames' if photo else Path(day)
            if (Path(relative).parent != expected or
                    (path.suffix != '.jpg' if photo else not path.name.startswith('timelapse-')) or
                    path.resolve() != path.absolute()):
                raise CameraError('보관 정리 경로가 저장소 규칙과 다릅니다.')
            return path

        with self.db() as db:
            dates = [row[0] for row in db.execute('SELECT day FROM frames UNION SELECT day FROM exports ORDER BY day')]
        for day in dates:
            if cancel is not None and cancel.is_set():
                raise CameraError("보관 정리가 취소됐습니다.")
            try:
                if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                    raise CameraError('보관 정리 날짜가 올바르지 않습니다.')
                folder = self.root / day
                if folder.resolve() != folder.absolute():
                    raise CameraError('보관 정리 날짜 폴더가 심볼릭 링크입니다.')
                rows = self.frames(day)
                with self.db() as db:
                    legacy = db.execute('SELECT 1 FROM exports WHERE day=? AND fps!=30', (day,)).fetchone()
                if day >= video_cutoff and rows and (day < photo_cutoff or legacy):
                    (self.render(day, cancel=cancel) if cancel is not None else self.render(day))  # Includes frame count, codec and duration verification.
                folder.mkdir(exist_ok=True)
                with (folder / '.render.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.db() as db:
                        rows = list(db.execute('SELECT * FROM frames WHERE day=? ORDER BY captured_at,id', (day,)))
                        exports = list(db.execute('SELECT * FROM exports WHERE day=?', (day,)))
                        summary = next((e for e in exports if e['fps'] == 30), None)
                        if rows and video_cutoff <= day < photo_cutoff:
                            source_hash = hashlib.sha256(''.join(r['sha256'] + r['path'] for r in rows).encode()).hexdigest()
                            if (not summary or summary['source_hash'] != source_hash or
                                    not checked(summary['path'], day).is_file() or
                                    checked(summary['path'], day).stat().st_size != summary['bytes']):
                                raise CameraError('검증된 하루 요약이 없어 사진을 보존합니다.')
                        # Validate every candidate before removing any file for this day.
                        photos = [(r, checked(r['path'], day, True)) for r in rows] if day < photo_cutoff else []
                        videos = [(e, checked(e['path'], day)) for e in exports if day < video_cutoff or e['fps'] != 30]
                        for row, path in photos:
                            path.unlink(missing_ok=True)
                            db.execute('DELETE FROM frames WHERE id=?', (row['id'],))
                            result['photos_deleted'] += 1
                        for export, path in videos:
                            path.unlink(missing_ok=True)
                            db.execute('DELETE FROM exports WHERE day=? AND fps=?', (day, export['fps']))
                            result['videos_deleted'] += 1
                frames_folder = folder / 'frames'
                if frames_folder.is_dir() and not any(frames_folder.iterdir()):
                    frames_folder.rmdir()
            except Exception as exc:
                result['errors'].append({'day': day, **self.record_error(exc)})
        with self.db() as db:
            db.execute('DELETE FROM errors WHERE at < ?', ((today - timedelta(days=30)).isoformat(),))
        return result

    def collect(self, factory, interval=30, max_frames=None, duration=None, emit=print,
                max_storage_mb=1024, min_free_mb=512, retention=False, maintenance=None):
        if not math.isfinite(interval) or interval < 1:
            raise CameraError("촬영 간격은 1초 이상이어야 합니다.")
        if max_frames is not None and max_frames < 1:
            raise CameraError("최대 컷 수는 1 이상이어야 합니다.")
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            raise CameraError("시험 시간은 0초 초과여야 합니다.")
        if any(not math.isfinite(v) or v <= 0 for v in (max_storage_mb, min_free_mb)):
            raise CameraError("저장 한도와 디스크 여유 기준은 0MB 초과여야 합니다.")
        stop = threading.Event()
        with (self.root / ".collector.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CameraError("이 저장소의 촬영기가 이미 실행 중입니다.") from None
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('storage_limits',?)", (json.dumps({'max_storage_mb': max_storage_mb, 'min_free_mb': min_free_mb}),))
            (self.root / ".stop").unlink(missing_ok=True)
            old_handlers = {}
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
            started = time.monotonic()
            due = started
            client = None
            count = 0
            last_day = None
            state = {"started_at": datetime.now(timezone.utc).isoformat(), "interval": interval,
                     "duration": duration, "max_frames": max_frames, "captured": 0,
                     "max_storage_mb": max_storage_mb, "min_free_mb": min_free_mb,
                     "state": "running"}
            reason = "stop_requested"
            backoff = Backoff()
            try:
                self.set_run(state)
                emit({"event": "started", "interval": interval, "root": str(self.root)})
                while not stop.is_set() and not (self.root / ".stop").exists():
                    elapsed = time.monotonic() - started
                    if duration is not None and elapsed >= duration:
                        reason = "duration_limit"
                        break
                    wait = due - time.monotonic()
                    if wait > 0:
                        stop.wait(min(wait, 0.5))
                        continue
                    today = datetime.now(timezone.utc).astimezone(KST).date().isoformat()
                    # Recover missed daily exports after a restart, then export at rollover.
                    if today != last_day:
                        maintenance_ready = True
                        if maintenance is not None:
                            try:
                                maintenance(self, retention)
                            except Exception as exc:
                                maintenance_ready = False
                                emit({'event': 'maintenance_deferred', **self.record_error(exc)})
                        else:
                            if retention:
                                emit({'event': 'retention', **self.prune()})
                            for day in self.days():
                                if day < today:
                                    try:
                                        emit({"event": "daily_export", **self.render(day)})
                                    except Exception as exc:
                                        emit({"event": "export_error", **self.record_error(exc)})
                        if maintenance_ready:
                            last_day = today
                    if (self.storage_bytes() >= max_storage_mb * 1024**2 or
                            shutil.disk_usage(self.root).free <= min_free_mb * 1024**2):
                        reason = "storage_limit"
                        break
                    try:
                        if client is None:
                            client = factory()
                        data = client.capture()
                        if (self.storage_bytes() + len(data) > max_storage_mb * 1024**2 or
                                shutil.disk_usage(self.root).free - len(data) < min_free_mb * 1024**2):
                            reason = "storage_limit"
                            break
                        frame = self.save(data, datetime.now(timezone.utc))
                        count += 1
                        if backoff.attempts:
                            emit({"event": "reconnected"})
                        backoff.reset()
                        state.update(captured=count, last_capture=frame["captured_at"],
                                     state="running", retry_in_seconds=None, connection_error=None)
                        self.set_run(state)
                        emit({"event": "captured", "count": count, **frame})
                    except Exception as exc:
                        emit({"event": "capture_error", **self.record_error(exc)})
                        if client:
                            client.close()
                            client = None
                        if not retryable(exc):
                            reason = "authentication_error" if safe_error(exc).get("kind") == "authentication" else "capture_failed"
                            break
                        delay = backoff.next()
                        state.update(state="waiting_for_camera", retry_in_seconds=delay,
                                     connection_error=safe_error(exc))
                        self.set_run(state)
                        emit({"event": "waiting_for_camera", "retry_in_seconds": delay})
                        due = time.monotonic() + delay
                        continue
                    if max_frames is not None and count >= max_frames:
                        reason = "frame_limit"
                        break
                    due += interval
                    # If requests/export took too long, skip missed slots instead of a burst.
                    now = time.monotonic()
                    if due <= now:
                        skipped = int((now - due) // interval) + 1
                        due += skipped * interval
                        emit({"event": "skipped_slots", "count": skipped})
            finally:
                if client:
                    client.close()
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)
                state.update(state="stopped", reason=reason, captured=count,
                             ended_at=datetime.now(timezone.utc).isoformat())
                self.set_run(state)
                emit({"event": "stopped", "captured": count, "reason": reason})
        return {"captured": count, "root": str(self.root), "reason": reason}
