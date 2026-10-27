"""Persistent, at-most-once daily clock sync in the Docker web process."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import threading

from .camera_clock import camera_clock, KST
from .config import redact, safe_error

from .paths import data_root
STATE_PATH = data_root() / "clock-sync.sqlite3"


class DailyClockSync:
    def __init__(self, config, path=STATE_PATH, *, synchronize=None, emit=None):
        self.config, self.path = config, Path(path)
        self.synchronize = synchronize or (lambda: camera_clock(config, sync=True))
        self.emit = emit or (lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
        self.stop_event = threading.Event()
        self.worker = None

    def _database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("""CREATE TABLE IF NOT EXISTS attempts (
            host TEXT NOT NULL, day TEXT NOT NULL, scheduled_for TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            detail TEXT, PRIMARY KEY(host, day))""")
        return db

    def tick(self, now=None):
        now = (now or datetime.now(KST)).astimezone(KST)
        scheduled = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now < scheduled:
            return None
        day = now.date().isoformat()
        db = self._database()
        try:
            # Commit the claim BEFORE contacting the camera. A crash or lost
            # reply must not cause a repeated write after a container restart.
            with db:
                claim = db.execute("INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,NULL,'started',NULL)",
                                   (self.config.host, day, scheduled.isoformat(), now.isoformat()))
            if claim.rowcount == 0:
                return None
            try:
                detail = self.synchronize()
                status = "success"
            except Exception as exc:
                detail, status = safe_error(exc), "failed"
            detail = redact(detail, self.config.password)
            with db:
                db.execute("UPDATE attempts SET finished_at=?, status=?, detail=? WHERE host=? AND day=?",
                           (datetime.now(KST).isoformat(), status, json.dumps(detail, ensure_ascii=False),
                            self.config.host, day))
            event = {"event": "camera_clock_daily", "scheduled_for": scheduled.isoformat(),
                     "status": status, "detail": detail}
            self.emit(event)
            return event
        finally:
            db.close()

    def status(self, now=None):
        now = (now or datetime.now(KST)).astimezone(KST)
        latest = None
        already_ran = False
        if self.path.exists():
            # Status never creates a DB or starts camera communication.
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                row = db.execute("SELECT scheduled_for, started_at, finished_at, status, detail "
                                 "FROM attempts WHERE host=? ORDER BY day DESC LIMIT 1",
                                 (self.config.host,)).fetchone()
                if row:
                    latest = dict(zip(("scheduled_for", "started_at", "finished_at", "status", "detail"), row))
                    latest["detail"] = json.loads(latest["detail"]) if latest["detail"] else None
                already_ran = db.execute("SELECT 1 FROM attempts WHERE host=? AND day=?",
                                         (self.config.host, now.date().isoformat())).fetchone() is not None
            finally:
                db.close()
        next_run = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if already_ran:
            next_run += timedelta(days=1)
        elif next_run < now:
            next_run = now  # Overdue: one catch-up on the next scheduler tick.
        return {"timezone": "Asia/Seoul", "daily_at": "04:00", "next_run": next_run.isoformat(),
                "last_attempt": latest}

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                # Storage failure must not bypass the durable daily claim.
                self.emit({"event": "camera_clock_scheduler_error", "error": safe_error(exc)})
            self.stop_event.wait(60)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._run, name="daily-camera-clock", daemon=True)
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=20)
