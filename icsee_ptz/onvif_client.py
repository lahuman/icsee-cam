"""Read-only ONVIF discovery using onvif-python 0.4.7 service APIs."""

from urllib.parse import urlsplit, urlunsplit

from requests.adapters import HTTPAdapter
from onvif import CacheMode
from onvif.services.devicemgmt import Device
from onvif.services.media import Media
from onvif.services.ptz import PTZ

from .config import ProtocolError, safe_error

MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"


def local_endpoint(url, host):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != host or parsed.username
            or parsed.password or parsed.fragment):
        raise ProtocolError("ONVIF 주소가 지정한 로컬 카메라의 HTTP 주소가 아닙니다.")
    return url


def advertised_endpoint(url, host, port):
    """Fix the observed XM stale XAddr host without contacting that host."""
    from .config import Config
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.port != port or not parsed.path.startswith("/onvif/")):
        raise ProtocolError("ONVIF 광고 주소의 포트/경로/형식이 예상과 다릅니다.")
    Config(host=parsed.hostname)  # Only accept private IPv4 advertisements.
    return urlunsplit(("http", f"{host}:{port}", parsed.path, "", ""))


class CameraOnlyAdapter(HTTPAdapter):
    def __init__(self, host):
        super().__init__(max_retries=0)
        self.host = host

    def send(self, request, **kwargs):
        local_endpoint(request.url, self.host)
        response = super().send(request, **kwargs)
        # A same-host redirect can also replay credentials to a different service.
        if response.is_redirect or response.is_permanent_redirect:
            response.close()
            raise ProtocolError("ONVIF HTTP 리다이렉트를 거부했습니다.")
        return response


class ONVIFProbe:
    def __init__(self, config, port, digest=False):
        self.config, self.port, self.digest = config, port, digest
        self.sessions = []

    def close(self):
        for session in self.sessions:
            session.close()

    def service(self, service_type, endpoint, authenticated=True):
        local_endpoint(endpoint, self.config.host)
        service = service_type(
            host=self.config.host, port=self.port, xaddr=endpoint,
            username=self.config.username if authenticated else None,
            password=self.config.password if authenticated else None,
            http_digest=self.digest, timeout=self.config.timeout, cache=CacheMode.MEM,
        )
        # Constructors load bundled local WSDLs; they do not send SOAP requests.
        # Pin this adapter to 0.4.7 and cover its transport access in tests.
        session = service.operator.client.transport.session
        session.trust_env = False  # Never send credentials through HTTP(S)_PROXY.
        session.mount("http://", CameraOnlyAdapter(self.config.host))
        session.mount("https://", CameraOnlyAdapter(self.config.host))
        self.sessions.append(session)
        return service

    def probe(self):
        result = {"port": self.port, "protocol": "onvif", "endpoint_confirmed": False,
                  "auth_mode_attempted": "http_digest" if self.digest else "ws_username_token",
                  "motor_tested": False, "control_enabled": False, "errors": []}
        endpoint = f"http://{self.config.host}:{self.port}/onvif/device_service"
        device = self.service(Device, endpoint, authenticated=bool(self.config.password))

        def query(label, call):
            try:
                value = call()
                result["endpoint_confirmed"] = True
                return value
            except Exception as exc:
                result["errors"].append({"operation": label, **safe_error(exc)})
                return None

        # A typed SOAP success, never a TCP connection, confirms the candidate path.
        clock = query("GetSystemDateAndTime", device.GetSystemDateAndTime)
        if any(e["kind"] == "authentication" for e in result["errors"]):
            return result
        services = query("GetServices", lambda: device.GetServices(IncludeCapability=False))
        if any(e["kind"] == "authentication" for e in result["errors"]):
            return result
        capabilities = query("GetCapabilities", lambda: device.GetCapabilities(Category="All"))
        result["endpoint"] = endpoint if result["endpoint_confirmed"] else None
        result["device_time_received"] = clock is not None
        result["credentials_supplied"] = bool(self.config.password)
        if not result["endpoint_confirmed"]:
            return result
        info = query("GetDeviceInformation", device.GetDeviceInformation)
        if info:
            result["device_information"] = {k: getattr(info, k, None) for k in
                ("Manufacturer", "Model", "FirmwareVersion", "HardwareId")}
        endpoints = {}
        if services:
            for service in services:
                ns, addr = getattr(service, "Namespace", None), getattr(service, "XAddr", None)
                if ns and addr:
                    endpoints[ns] = addr
        if capabilities:
            # Only use actually advertised Media and PTZ addresses, no guessed fallback.
            for name, ns in (("Media", MEDIA_NS), ("PTZ", PTZ_NS)):
                address = getattr(getattr(capabilities, name, None), "XAddr", None)
                if address:
                    endpoints.setdefault(ns, address)
            from zeep.helpers import serialize_object
            result["capabilities"] = serialize_object(capabilities)
        result["advertised_services"] = endpoints
        resolved = {ns: advertised_endpoint(addr, self.config.host, self.port)
                    for ns, addr in endpoints.items() if ns in (MEDIA_NS, PTZ_NS)}
        result["resolved_services"] = resolved
        if MEDIA_NS not in endpoints:
            result["ptz_evidence"] = "media_endpoint_not_advertised"
            return result
        media = self.service(Media, resolved[MEDIA_NS])
        profiles = query("GetProfiles", media.GetProfiles)
        from zeep.helpers import serialize_object
        result["profiles"] = [{"profile_token": profile.token, "name": profile.Name,
            "ptz_configuration": serialize_object(getattr(profile, "PTZConfiguration", None))}
            for profile in profiles or []]
        if PTZ_NS not in endpoints:
            result["ptz_evidence"] = "ptz_endpoint_not_advertised"
            return result
        ptz = self.service(PTZ, resolved[PTZ_NS])
        configurations = query("GetConfigurations", ptz.GetConfigurations)
        nodes = query("GetNodes", ptz.GetNodes)
        result["ptz_configurations"] = serialize_object(configurations)
        result["ptz_nodes"] = serialize_object(nodes)
        for profile, item in zip(profiles or [], result["profiles"]):
            config = getattr(profile, "PTZConfiguration", None)
            if config is not None:
                options = query("GetConfigurationOptions", lambda: ptz.GetConfigurationOptions(
                    ConfigurationToken=config.token))
                item["options"] = serialize_object(options)
                spaces = getattr(options, "Spaces", None)
                item["continuous_pan_tilt_advertised"] = bool(getattr(
                    spaces, "ContinuousPanTiltVelocitySpace", None))
        result["ptz_evidence"] = "capabilities_only_motor_unverified"
        # WS-UsernameToken mode may also succeed on a device with no authentication.
        result["auth_note"] = "호출 성공만으로 인증 강제 여부나 모터 동작은 확정할 수 없습니다."
        return result


def probe_onvif(config, port):
    attempts = []
    for digest in (False, True):
        probe = ONVIFProbe(config, port, digest)
        try:
            result = probe.probe()
        except Exception as exc:
            result = {"port": port, "protocol": "onvif", "control_enabled": False,
                      "endpoint_confirmed": False, "errors": [safe_error(exc)]}
        finally:
            probe.close()
        attempts.append(result)
        # Retry once with HTTP Digest only when WS authentication was rejected.
        # Authentication lockouts are possible; no loops or password guessing.
        if not config.password or not any(e.get("kind") == "authentication"
                                         for e in result.get("errors", [])):
            break
    return attempts
