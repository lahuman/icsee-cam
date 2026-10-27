from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import Mock
import sqlite3
import threading

from fastapi.testclient import TestClient

from icsee_ptz.camera_clock import KST
from icsee_ptz.config import Config
from icsee_ptz.daily_clock import DailyClockSync
from icsee_ptz.web import create_app


def scheduler(path, synchronize=None):
    return DailyClockSync(Config(password="scheduler-secret"), path,
                          synchronize=synchronize or Mock(return_value={"synchronized": True}), emit=Mock())


def test_daily_boundary_catchup_restart_and_next_run(tmp_path):
    path = tmp_path / "clock.sqlite3"
    sync = Mock(return_value={"synchronized": True})
    daily = scheduler(path, sync)
    assert daily.tick(datetime(2026, 10, 26, 3, 59, tzinfo=KST)) is None
    assert not path.exists()
    due = datetime(2026, 10, 26, 4, 0, tzinfo=KST)
    assert daily.tick(due)["status"] == "success"
    assert daily.tick(due) is None
    restarted = scheduler(path, sync)
    assert restarted.tick(datetime(2026, 10, 26, 23, 59, tzinfo=KST)) is None
    status = restarted.status(due)
    assert status["daily_at"] == "04:00"
    assert status["next_run"] == "2026-10-27T04:00:00+09:00"
    assert status["last_attempt"]["status"] == "success"
    # Late restart catches up once for the CURRENT day, without replaying old days.
    assert restarted.tick(datetime(2026, 10, 29, 10, 0, tzinfo=KST))["status"] == "success"
    assert sync.call_count == 2


def test_korean_time_independent_of_container_timezone(tmp_path):
    daily = scheduler(tmp_path / "clock.sqlite3")
    assert daily.tick(datetime(2026, 10, 25, 18, 59, tzinfo=timezone.utc)) is None
    assert daily.tick(datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc))["scheduled_for"] == "2026-10-26T04:00:00+09:00"


def test_failed_or_uncertain_write_is_recorded_without_daily_retry(tmp_path):
    path = tmp_path / "clock.sqlite3"
    sync = Mock(side_effect=TimeoutError())
    daily = scheduler(path, sync)
    now = datetime(2026, 10, 26, 10, 0, tzinfo=KST)
    assert daily.tick(now)["status"] == "failed"
    assert daily.status(now)["last_attempt"]["detail"]["kind"] == "timeout"
    assert scheduler(path, sync).tick(now) is None
    sync.assert_called_once()
    assert daily.tick(now.replace(day=27))["status"] == "failed"
    assert sync.call_count == 2


def test_claim_is_durable_before_camera_contact_and_across_workers(tmp_path):
    path = tmp_path / "clock.sqlite3"
    now = datetime(2026, 10, 26, 4, 0, tzinfo=KST)
    entered, finish = threading.Event(), threading.Event()
    def sync():
        db = sqlite3.connect(path)
        try:
            assert db.execute("SELECT status FROM attempts").fetchone()[0] == "started"
        finally:
            db.close()
        entered.set()
        assert finish.wait(2)
        return {"synchronized": True}
    first, second = scheduler(path, sync), scheduler(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.tick, now)
        assert entered.wait(1)
        try:
            assert second.tick(now) is None
            second.synchronize.assert_not_called()
        finally:
            finish.set()
        assert pending.result()["status"] == "success"


def test_storage_failure_never_contacts_camera(tmp_path):
    path = tmp_path / "blocked"
    path.write_text("not a directory")
    daily = scheduler(path / "clock.sqlite3")
    try:
        daily.tick(datetime(2026, 10, 26, 4, 0, tzinfo=KST))
    except OSError:
        pass
    else:
        raise AssertionError("storage should fail")
    daily.synchronize.assert_not_called()


def test_status_does_not_create_database_or_connect_to_camera(tmp_path):
    path = tmp_path / "clock.sqlite3"
    daily = scheduler(path)
    state = daily.status(datetime(2026, 10, 26, 3, 0, tzinfo=KST))
    assert state["last_attempt"] is None
    assert state["next_run"] == "2026-10-26T04:00:00+09:00"
    assert not path.exists()
    daily.synchronize.assert_not_called()


def test_web_lifecycle_starts_and_stops_only_configured_scheduler():
    daily = Mock()
    app = create_app(allow_unauthenticated=True, config=Config(password="test"), manager=Mock(), clock_scheduler=daily)
    with TestClient(app):
        daily.start.assert_called_once()
        daily.close.assert_not_called()
    daily.close.assert_called_once()
