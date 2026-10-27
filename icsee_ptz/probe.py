from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
import platform
import socket
import sys

from .config import safe_error
from .dvrip import DVRIPClient
from .onvif_client import probe_onvif


def tcp_probe(config, port):
    try:
        with socket.create_connection((config.host, port), config.timeout):
            return {"port": port, "tcp": "open"}
    except OSError as exc:
        return {"port": port, "tcp": "failed", **safe_error(exc)}


def probe(config):
    ports = (80, 554, 8899, 34567)
    with ThreadPoolExecutor(max_workers=4) as pool:
        network = list(pool.map(lambda port: tcp_probe(config, port), ports))
    opened = {entry["port"] for entry in network if entry["tcp"] == "open"}
    report = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": config.host, "python": sys.version.split()[0],
        "executable": sys.executable, "machine": platform.machine(),
        "onvif_python": version("onvif-python"),
        "password_present": bool(config.password), "network": network,
        "onvif": [], "dvrip": {"state": "not_reachable"},
        "active_protocol": None, "motor_tested": False,
    }
    # ONVIF is always tried first; neither discovery path sends motor commands.
    for port in (8899, 80):
        if port in opened:
            report["onvif"].extend(probe_onvif(config, port))
    if 34567 in opened:
        if not config.password:
            report["dvrip"] = {"state": "missing_password"}
        else:
            try:
                with DVRIPClient(config) as client:
                    result = {"state": "authenticated", "motor_tested": False,
                              "control_enabled": False}
                    for name in ("SystemInfo", "SystemFunction"):
                        try:
                            result[name] = client.read(name)
                        except Exception as exc:
                            result[name] = {"error": safe_error(exc)}
                    report["dvrip"] = result
            except Exception as exc:
                report["dvrip"] = {"state": "failed", "error": safe_error(exc)}
    from .verification import verified_profile
    verified = verified_profile(config)
    info = report["dvrip"].get("SystemInfo", {})
    if (verified and info.get("HardWare") == verified["hardware"]
            and info.get("SoftWareVersion") == verified["firmware"]):
        report["active_protocol"] = "dvrip"
        report["dvrip"]["control_enabled"] = True
        report["dvrip"]["previous_motor_verification"] = verified
    if not opened:
        report["next_step"] = "카메라 전원/IP, 동일 LAN, AP 격리, macOS 로컬 네트워크 권한을 확인하세요. WAN 차단은 유지하세요."
    elif not config.password:
        report["next_step"] = "ICSEE_PASSWORD를 설정한 터미널에서 다시 실행하세요."
    else:
        report["next_step"] = ("DVRIP 검증 기록과 장비가 일치합니다. 이동 명령에서만 모터가 동작합니다."
                               if report["active_protocol"] else
                               "서비스 응답 검토 후 명시적으로 승인받은 모터 검증이 필요합니다.")
    return report
