from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import os


class CameraError(Exception):
    """Safe, locally generated error text; never include remote response bodies."""


class AuthenticationError(CameraError):
    pass


class ProtocolError(CameraError):
    pass


@dataclass(frozen=True)
class Config:
    host: str = "192.168.1.168"
    username: str = "admin"
    password: str | None = field(default=None, repr=False)
    timeout: int = 3

    def __post_init__(self):
        # Literal RFC1918 addresses only: no DNS, WAN, multicast or loopback.
        try:
            addr = ip_address(self.host)
        except ValueError:
            raise CameraError("카메라 주소는 사설 IPv4 주소여야 합니다.") from None
        if not any(addr in ip_network(net) for net in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            raise CameraError("카메라는 RFC1918 로컬 IPv4 주소만 허용합니다.")
        if not 1 <= self.timeout <= 10:
            raise CameraError("timeout은 1~10초여야 합니다.")

    @classmethod
    def from_env(cls, host=None, timeout=None):
        settings = camera_settings()
        return cls(host or os.environ.get("ICSEE_HOST") or settings.get("host", "192.168.1.168"),
                   os.environ.get("ICSEE_USERNAME") or settings.get("username", "admin"),
                   os.environ.get("ICSEE_PASSWORD") or None,
                   timeout if timeout is not None else settings.get("timeout", 3))

    def require_password(self):
        if self.password is None:
            raise AuthenticationError(
                "ICSEE_PASSWORD 환경 변수가 없습니다. 비밀번호를 설정한 터미널에서 실행하세요.")


def camera_settings():
    """Only non-secret connection settings are persisted; legacy installs still work."""
    import json
    from .paths import config_root
    path = config_root() / "camera.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1
                or set(data) - {"version", "host", "username", "timeout"}
                or not isinstance(data.get("host"), str)
                or not isinstance(data.get("username"), str)
                or not 1 <= len(data["username"]) <= 64
                or type(data.get("timeout")) is not int):
            raise ValueError()
        Config(host=data["host"], username=data["username"], timeout=data["timeout"])
        return data
    except (OSError, ValueError, TypeError, CameraError):
        raise CameraError("config/camera.json이 올바르지 않습니다. host, username, timeout을 확인하세요.") from None


def safe_error(exc):
    """Classify without printing exceptions that can contain SOAP or credentials."""
    import requests
    from zeep.exceptions import Fault, TransportError

    error = getattr(exc, "original_exception", exc)
    if isinstance(error, AuthenticationError):
        return {"kind": "authentication", "message": str(error)}
    if isinstance(error, ProtocolError):
        return {"kind": "protocol", "type": "ProtocolError", "message": str(error)}
    if isinstance(error, requests.Timeout) or isinstance(error, TimeoutError):
        return {"kind": "timeout"}
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    if status in (401, 403):
        return {"kind": "authentication", "http_status": status}
    if isinstance(error, Fault):
        text = str(error).lower()
        return {"kind": "authentication" if any(word in text for word in
                ("notauthorized", "not authorized", "unauthorized", "failedauthentication"))
                else "soap_fault"}
    if isinstance(error, PermissionError):
        return {"kind": "local_permission", "errno": error.errno}
    if isinstance(error, (requests.ConnectionError, OSError)):
        return {"kind": "connection", "errno": getattr(error, "errno", None)}
    if isinstance(error, (ProtocolError, TransportError, requests.RequestException)):
        return {"kind": "protocol", "type": type(error).__name__}
    return {"kind": "client", "type": type(error).__name__}


def redact(value, password=None):
    """Scrub credentials and session tokens before any output or report storage."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if str(k).lower().replace("_", "") in
                ("password", "passwordv2", "passwd", "session", "sessionid", "secret",
                 "token", "authorization", "hashpass", "passwordhash")
                else redact(v, password) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(v, password) for v in value]
    if isinstance(value, str) and password:
        return value.replace(password, "[REDACTED]")
    return value
