"""Read-only ONVIF snapshot acquisition. No PTZ client or motor operations."""
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.auth import HTTPDigestAuth

from .config import Config, ProtocolError
from .onvif_client import ONVIFProbe, Device, Media, MEDIA_NS, advertised_endpoint, CameraOnlyAdapter


class SnapshotClient:
    def __init__(self, config):
        config.require_password()
        self.config = config
        self.probe = ONVIFProbe(config, 8899)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = HTTPDigestAuth(config.username, config.password)
        self.session.mount("http://", CameraOnlyAdapter(config.host))
        try:
            device = self.probe.service(Device, f"http://{config.host}:8899/onvif/device_service")
            services = device.GetServices(IncludeCapability=False)
            address = next((s.XAddr for s in services if s.Namespace == MEDIA_NS), None)
            if not address:
                raise ProtocolError("ONVIF Media 서비스가 광고되지 않았습니다.")
            media = self.probe.service(Media, advertised_endpoint(address, config.host, 8899))
            profiles = media.GetProfiles()
            if not profiles:
                raise ProtocolError("스냅샷 profile이 없습니다.")
            uri = media.GetSnapshotUri(ProfileToken=profiles[0].token).Uri
            parsed = urlsplit(uri)
            Config(host=parsed.hostname)  # Refuse external/DNS advertisements.
            if parsed.scheme != "http" or parsed.port not in (None, 80, 8899) or parsed.fragment:
                raise ProtocolError("스냅샷 주소의 형식/포트가 예상과 다릅니다.")
            # Retain the actual advertised snapshot path, pin requests to the camera.
            self.url = urlunsplit(("http", f"{config.host}:{parsed.port or 80}",
                                   parsed.path, parsed.query, ""))
        except BaseException:
            self.close()
            raise

    def capture(self):
        with self.session.get(self.url, timeout=self.config.timeout, stream=True) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 8 * 1024 * 1024:
                    raise ProtocolError("스냅샷이 8MiB 제한을 초과했습니다.")
            data = bytes(content)
            if not data.startswith(b"\xff\xd8") or not data.rstrip().endswith(b"\xff\xd9"):
                raise ProtocolError("정상 JPEG 스냅샷 응답이 아닙니다.")
            return data

    def close(self):
        self.session.close()
        self.probe.close()
