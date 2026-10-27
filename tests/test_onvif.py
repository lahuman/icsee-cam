from types import SimpleNamespace as N
from unittest.mock import patch
import pytest
from requests import Request, Response
from icsee_ptz.config import Config, ProtocolError
from icsee_ptz.onvif_client import (ONVIFProbe, Device, Media, PTZ, CameraOnlyAdapter,
                                    local_endpoint, MEDIA_NS, PTZ_NS)


@pytest.mark.parametrize("url", ["http://8.8.8.8/onvif/ptz", "http://camera.local/onvif",
    "http://admin:secret@192.168.1.168/onvif/ptz", "https://192.168.1.168/onvif/ptz"])
def test_advertised_endpoint_cannot_escape_camera(url):
    with pytest.raises(ProtocolError):
        local_endpoint(url, "192.168.1.168")


@pytest.mark.parametrize("cls", [Device, Media, PTZ])
def test_installed_library_transport_and_bundled_wsdl(cls):
    probe = ONVIFProbe(Config(password="test-only"), 8899)
    with patch("socket.socket.connect", side_effect=AssertionError("network at construction")):
        service = probe.service(cls, "http://192.168.1.168:8899/onvif/device_service")
    session = service.operator.client.transport.session
    assert session.trust_env is False
    assert isinstance(session.get_adapter("http://192.168.1.168"), CameraOnlyAdapter)
    probe.close()


def test_onvif_discovery_never_calls_motor_methods():
    device = N(GetSystemDateAndTime=lambda: N(), GetDeviceInformation=lambda: N(Model="XM"),
               GetServices=lambda **_: [N(Namespace=MEDIA_NS, XAddr="http://192.168.1.10:8899/onvif/media_service"),
                                        N(Namespace=PTZ_NS, XAddr="http://192.168.1.10:8899/onvif/ptz_service")],
               GetCapabilities=lambda **_: N())
    media = N(GetProfiles=lambda: [N(token="p1", Name="Main", PTZConfiguration=N(token="c1"))])
    ptz = N(GetConfigurations=lambda: [], GetNodes=lambda: [],
            GetConfigurationOptions=lambda **_: N(Spaces=N(ContinuousPanTiltVelocitySpace=[1])))
    probe = ONVIFProbe(Config(password="test-only"), 8899)
    with patch.object(probe, "service", side_effect=[device, media, ptz]):
        result = probe.probe()
    assert result["endpoint_confirmed"]
    assert result["profiles"][0]["continuous_pan_tilt_advertised"]
    assert result["control_enabled"] is False
    assert result["motor_tested"] is False


def test_open_http_is_not_onvif_evidence():
    def fail(*args, **kwargs):
        raise ProtocolError("HTML is not SOAP")
    device = N(GetSystemDateAndTime=fail, GetServices=fail, GetCapabilities=fail)
    probe = ONVIFProbe(Config(), 80)
    with patch.object(probe, "service", return_value=device):
        result = probe.probe()
    assert result["endpoint_confirmed"] is False


def test_redirect_is_blocked_before_following():
    response = Response()
    response.status_code = 302
    response.headers["location"] = "http://8.8.8.8/"
    response._content = b""
    response._content_consumed = True
    request = Request("POST", "http://192.168.1.168/onvif/device_service").prepare()
    with patch("requests.adapters.HTTPAdapter.send", return_value=response):
        with pytest.raises(ProtocolError):
            CameraOnlyAdapter("192.168.1.168").send(request)


def test_real_zeep_soap_round_trip_without_camera():
    response = Response()
    response.status_code = 200
    response.headers["Content-Type"] = "application/soap+xml; charset=utf-8"
    response._content = b'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
      xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body>
      <tds:GetDeviceInformationResponse><tds:Manufacturer>XM</tds:Manufacturer>
      <tds:Model>XM-102</tds:Model><tds:FirmwareVersion>test</tds:FirmwareVersion>
      <tds:SerialNumber>test</tds:SerialNumber><tds:HardwareId>RH50X20</tds:HardwareId>
      </tds:GetDeviceInformationResponse></s:Body></s:Envelope>'''
    response._content_consumed = True
    probe = ONVIFProbe(Config(password="test-only"), 8899)
    service = probe.service(Device, "http://192.168.1.168:8899/onvif/device_service")
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        info = service.GetDeviceInformation()
    probe.close()
    assert info.Model == "XM-102"
    request = send.call_args.args[0]
    assert request.url == "http://192.168.1.168:8899/onvif/device_service"
    assert b"GetDeviceInformation" in request.body
    assert b"PasswordDigest" in request.body
    assert b"test-only" not in request.body
