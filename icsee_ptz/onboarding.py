"""Local setup and explicit device enrollment, without automatic motor tests."""
from datetime import datetime, timezone
import getpass
import json
import os
import shutil
import tempfile

from .accounts import password_record, read_accounts, write_accounts
from .config import CameraError, Config, camera_settings
from .paths import config_root, data_root, project_root
from .verification import profile_path, read_profile, verified_profile

DIRECTIONS = ("left", "right", "up", "down")


def save_json(path, data, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".setup-", delete=False) as stream:
        temporary = type(path)(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, path)
            else:
                # Atomic create: never truncate a file that appeared during setup.
                os.link(temporary, path)
        except FileExistsError:
            raise CameraError("설정 파일이 이미 있습니다. 기존 파일을 덮어쓰지 않습니다.") from None
        finally:
            temporary.unlink(missing_ok=True)


def setup_installation(host=None, username=None):
    root = config_root()
    settings_path, auth_path = root / "camera.json", root / "web-auth.json"
    if settings_path.exists():
        settings = camera_settings()
        if (host and host != settings["host"]) or (username and username != settings["username"]):
            raise CameraError("이미 설정된 장비가 있습니다. 다른 장비는 별도 ICSEE_PROJECT_DIR에서 설정하세요.")
    else:
        # Starting a new profile must not shadow an existing operational profile.
        if (project_root() / "camera-profile.json").exists():
            raise CameraError("기존 운영 프로필이 있습니다. 새 설치는 별도 ICSEE_PROJECT_DIR에서 setup을 실행하세요.")
        host = host or os.environ.get("ICSEE_HOST") or input("카메라 IP (예: 192.168.1.100): ").strip()
        username = username or os.environ.get("ICSEE_USERNAME") or input("카메라 로그인 ID [admin]: ").strip() or "admin"
        Config(host=host, username=username)
        if not 1 <= len(username) <= 64:
            raise CameraError("카메라 ID는 1~64자여야 합니다.")
        settings = {"version": 1, "host": host, "username": username, "timeout": 3}
    users = None
    if auth_path.exists():
        read_accounts(auth_path)
    else:
        name = input("새 웹 관리자 ID [admin]: ").strip() or "admin"
        if not 1 <= len(name) <= 64:
            raise CameraError("웹 ID는 1~64자여야 합니다.")
        password = getpass.getpass("새 웹 비밀번호 (8~128자, 카메라 비밀번호와 별도): ")
        if password != getpass.getpass("웹 비밀번호 확인: "):
            raise CameraError("비밀번호가 일치하지 않습니다. 설정을 저장하지 않았습니다.")
        users = {name: {"username": name, "role": "admin", "enabled": True,
                        "password_hash": password_record(password)}}
    if not settings_path.exists():
        save_json(settings_path, settings)
    if users is not None:
        # Serialize with the existing account administration CLI.
        import fcntl
        with auth_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not auth_path.exists():
                write_accounts(auth_path, users)
    return {"state": "configured", "host": settings["host"],
            "next_step": "ICSEE_PASSWORD를 환경 변수로 설정한 뒤 doctor → register → serve를 실행하세요.",
            "camera_password_saved": False, "motor_tested": False}


def doctor(host=None, timeout=None):
    checks = []
    def check(name, ok, message):
        checks.append({"name": name, "ok": bool(ok), "message": message})
    config = None
    try:
        config = Config.from_env(host, timeout)
        configured = bool(host or os.environ.get("ICSEE_HOST") or camera_settings() or verified_profile(config))
        check("camera", configured, "카메라 주소 설정됨" if configured else "setup으로 카메라 주소를 설정하세요.")
    except CameraError as exc:
        check("camera", False, str(exc))
    check("password", bool(os.environ.get("ICSEE_PASSWORD")), "ICSEE_PASSWORD 환경 변수의 존재만 검사합니다.")
    try:
        read_accounts(config_root() / "web-auth.json")
        check("web_account", True, "활성 웹 관리자 계정 확인")
    except CameraError:
        check("web_account", False, "setup 또는 accounts set --role admin으로 웹 계정을 생성하세요.")
    for name in ("ffmpeg", "ffprobe"):
        check(name, shutil.which(name) is not None, f"{name} 실행 파일이 PATH에 필요합니다.")
    try:
        data_root().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=data_root()):
            pass
        check("data_directory", True, "데이터 디렉터리 쓰기 가능")
    except OSError:
        check("data_directory", False, "데이터 디렉터리의 경로와 쓰기 권한을 확인하세요.")
    profile = read_profile()
    registered = bool(config and profile and profile.get("host") == config.host)
    return {"ready": all(item["ok"] for item in checks), "checks": checks,
            "registered": registered, "ptz_verified": bool(config and verified_profile(config)),
            "network_checked": False,
            "next_step": "register로 장비를 확인한 뒤 serve로 영상을 여세요. PTZ는 verify-motion 후 활성화됩니다."}


def device_identity(config):
    from .dvrip import DVRIPClient
    config.require_password()
    with DVRIPClient(config) as client:
        info = client.read("SystemInfo")
    if not isinstance(info, dict) or not all(isinstance(info.get(key), str) and info[key]
                                            for key in ("HardWare", "SoftWareVersion")):
        raise CameraError("DVRIP 장비 식별 정보를 확인하지 못했습니다. probe로 지원 프로토콜을 확인하세요.")
    # Never persist raw reports, serial numbers, session IDs or credentials.
    return {"host": config.host, "hardware": info["HardWare"],
            "firmware": info["SoftWareVersion"], "protocol": "dvrip"}


def register_device(config):
    identity = device_identity(config)
    previous = read_profile()
    if profile_path().exists():
        if not previous or any(previous.get(key) != value for key, value in identity.items()):
            raise CameraError("기존 프로필과 장비가 다릅니다. 별도 설치 디렉터리에서 등록하세요. 기존 검증 기록은 보존했습니다.")
        return {"state": "already_registered", **identity, "ptz_verified": bool(verified_profile(config))}
    save_json(profile_path(), {"version": 1, **identity,
             "registered_at": datetime.now(timezone.utc).isoformat(),
             "physical_motion_and_stop": "not_tested"})
    return {"state": "registered", **identity, "ptz_verified": False,
            "next_step": "serve로 영상을 확인하세요. 방향 제어는 별도 verify-motion이 필요합니다."}


def verify_motion(config, start_mode):
    """Each bounded motion needs an explicit local prompt and observed STOP."""
    import fcntl
    from .dvrip_control import DVRIPControl
    from .paths import runtime_root
    profile = read_profile()
    identity = device_identity(config)
    if not profile or any(profile.get(key) != value for key, value in identity.items()):
        raise CameraError("먼저 register로 현재 장비를 등록하세요.")
    # Use the web server's lock so a second user cannot move during enrollment.
    runtime_root().mkdir(parents=True, exist_ok=True)
    with (runtime_root() / "web-instance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CameraError("웹 서버를 중지한 뒤 검증하세요. 카메라를 직접 보면서 진행하세요.") from None
        direction_map = {}
        for direction in DIRECTIONS:
            if input(f"카메라를 직접 보세요. {direction} 명령으로 0.5초 이동하려면 MOVE 입력: ").strip() != "MOVE":
                raise CameraError("검증을 중단했습니다. 프로필을 변경하지 않았습니다.")
            controller = DVRIPControl(config, legacy_start=start_mode == "legacy", verified=identity)
            try:
                result = controller.move(direction, 0.5, 0.25)
            finally:
                controller.close()
            if (result.get("move_response") != "accepted" or not result.get("stop_acknowledged_by")
                    or result.get("stop_errors")):
                raise CameraError("이동 또는 STOP 응답을 확인하지 못했습니다. 검증을 중단하고 장비 상태를 확인하세요.")
            observed = input("실제로 움직인 방향 (left/right/up/down, 안 움직였거나 불확실하면 Enter): ").strip()
            if observed not in DIRECTIONS or observed in direction_map:
                raise CameraError("방향을 확인하지 못했습니다. 프로필을 변경하지 않았습니다.")
            if input("카메라가 실제로 멈춘 것을 확인했으면 STOP 입력: ").strip() != "STOP":
                raise CameraError("실제 정지를 확인하지 못했습니다. 프로필을 변경하지 않았습니다.")
            direction_map[observed] = direction
        # Refuse to overwrite a concurrently changed or replaced device/profile.
        if read_profile() != profile or device_identity(config) != identity:
            raise CameraError("검증 중 장비 또는 프로필이 변경되었습니다. 저장하지 않았습니다.")
        updated = {**profile, "legacy_start": start_mode == "legacy", "tested_step": 2,
                   "verified_duration": 0.5, "verified_directions": list(DIRECTIONS),
                   "direction_map": direction_map, "physical_motion_and_stop": "confirmed_by_user",
                   "verified_at": datetime.now(timezone.utc).isoformat()}
        save_json(profile_path(), updated, replace=True)
    return {"state": "verified", "ptz_verified": True, "direction_map": direction_map,
            "next_step": "serve를 다시 시작하면 방향 제어가 활성화됩니다. 프리셋 이동은 별도 검증이 필요합니다."}
