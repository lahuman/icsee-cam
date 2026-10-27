import json
from unittest.mock import patch
import pytest
from icsee_ptz.config import Config, ProtocolError, AuthenticationError
from icsee_ptz.dvrip import DVRIPClient, HEADER, MAX_BODY, sofia_hash


class Socket:
    def __init__(self, incoming):
        self.incoming = incoming
        self.sent = []
        self.closed = False

    def recv(self, n):
        # Exercise fragmented TCP headers and bodies.
        chunk = self.incoming[:min(n, 3)]
        self.incoming = self.incoming[len(chunk):]
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True

    def settimeout(self, *_):
        pass


def packet(msg, data, seq=0, session=1):
    body = json.dumps(data).encode() + b"\n\x00"
    return HEADER.pack(255, 0, session, seq, msg, len(body)) + body


def test_sofia_hash_known_vector():
    assert sofia_hash("") == "tlJwpbo6"


def test_login_and_reads_fragmented_no_motor_packets():
    incoming = packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
    incoming += packet(1021, {"Ret": 100, "SystemInfo": {"BuildTime": "test"}}, 1)
    incoming += packet(1361, {"Ret": 100, "SystemFunction": {"PTZ": True}}, 2)
    sock = Socket(incoming)
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            assert client.read("SystemInfo")["BuildTime"] == "test"
            assert client.read("SystemFunction")["PTZ"] is True
            with pytest.raises(ProtocolError):
                client.read("OPPTZControl")
            with pytest.raises(ProtocolError):
                client._exchange(1040, {})
    assert sock.closed
    assert [HEADER.unpack(p[:20])[4] for p in sock.sent] == [1000, 1020, 1360]
    assert b"test-only" not in sock.sent[0]


def test_auth_failure_closes_without_retry():
    sock = Socket(packet(1001, {"Ret": 106}))
    with patch("socket.create_connection", return_value=sock):
        with pytest.raises(AuthenticationError):
            with DVRIPClient(Config(password="test-only")):
                pytest.fail("must not enter")
    assert len(sock.sent) == 1
    assert sock.closed


def test_storage_inspection_uses_only_status_and_config_gets():
    names = [("StorageInfo", 1020), ("Record", 1042),
             ("Detect.MotionDetect", 1042), ("Simplify.Encode", 1042), ("WorkState", 1020)]
    incoming = packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
    for seq, (name, message) in enumerate(names, 1):
        incoming += packet(message + 1, {"Ret": 100, name: []}, seq)
    sock = Socket(incoming)
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            for name, _ in names:
                assert client.read(name) == []
            for name in ("OPStorageManager", "Record.Start", "Users"):
                with pytest.raises(ProtocolError):
                    client.read(name)
            with pytest.raises(ProtocolError):
                client._exchange(1040, {"Name": "Record"})
    assert [HEADER.unpack(p[:20])[4] for p in sock.sent] == [1000, 1020, 1042, 1042, 1042, 1020]
    assert sock.closed


@pytest.mark.parametrize("response", [
    b"",  # premature EOF
    HEADER.pack(255, 0, 1, 0, 1001, MAX_BODY + 1),
    HEADER.pack(255, 0, 1, 0, 1001, 0),
    packet(1001, {"Ret": 100}, seq=99),
    packet(1401, {"Ret": 100}),
    packet(1001, {"Ret": 100, "SessionID": "0x2"}),
])
def test_malformed_response_fails_closed(response):
    sock = Socket(response)
    with patch("socket.create_connection", return_value=sock):
        with pytest.raises(ProtocolError):
            with DVRIPClient(Config(password="test-only")):
                pass
    assert sock.closed


def test_no_password_no_network():
    with patch("socket.create_connection") as connect:
        with pytest.raises(AuthenticationError):
            with DVRIPClient(Config()):
                pass
    connect.assert_not_called()


def test_ptz_wire_start_and_stop():
    sock = Socket(packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
                  + packet(1401, {"Ret": 100}, 1) + packet(1401, {"Ret": 100}, 2))
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            client.ptz("right", step=1)
            client.ptz("up", stop=True)
    start = json.loads(sock.sent[1][20:-2])["OPPTZControl"]
    stop = json.loads(sock.sent[2][20:-2])["OPPTZControl"]
    assert start["Command"] == "DirectionRight"
    assert start["Parameter"]["Preset"] == 65535
    assert start["Parameter"]["Step"] == 1
    assert stop["Command"] == "DirectionUp"
    assert stop["Parameter"]["Preset"] == -1
    assert stop["Parameter"]["Channel"] == start["Parameter"]["Channel"] == 0


def test_tracking_write_preserves_settings_and_reads_back():
    name = "Detect.DetectTrack"
    settings = {"Enable": 0, "ReturnTime": 300, "Sensitivity": 2, "FutureField": {"x": 9}}
    incoming = packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
    incoming += packet(1043, {"Ret": 100, name: settings}, 1)
    incoming += packet(1041, {"Ret": 100}, 2)
    incoming += packet(1043, {"Ret": 100, name: {**settings, "Enable": 1}}, 3)
    sock = Socket(incoming)
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            assert client.set_tracking(True) == {"enabled": True}
    assert [HEADER.unpack(p[:20])[4] for p in sock.sent] == [1000, 1042, 1040, 1042]
    assert json.loads(sock.sent[2][20:-2])[name] == {**settings, "Enable": 1}
    assert sock.closed


@pytest.mark.parametrize("ret,actual,error", [
    (100, 0, ProtocolError),  # ACK alone is insufficient.
    (106, None, AuthenticationError),
    (603, None, ProtocolError),  # No automatic reboot.
    (104, None, ProtocolError),
])
def test_tracking_failure_never_retries_write(ret, actual, error):
    name = "Detect.DetectTrack"
    incoming = packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
    incoming += packet(1043, {"Ret": 100, name: {"Enable": 0}}, 1)
    incoming += packet(1041, {"Ret": ret}, 2)
    if actual is not None:
        incoming += packet(1043, {"Ret": 100, name: {"Enable": actual}}, 3)
    sock = Socket(incoming)
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            with pytest.raises(error):
                client.set_tracking(True)
    assert sum(HEADER.unpack(p[:20])[4] == 1040 for p in sock.sent) == 1


@pytest.mark.parametrize("settings", [[], {}, {"Enable": "0"}, {"Enable": 2}, {"Enable": 0.0}])
def test_unknown_tracking_schema_never_writes(settings):
    sock = Socket(packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
                  + packet(1043, {"Ret": 100, "Detect.DetectTrack": settings}, 1))
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            with pytest.raises(ProtocolError):
                client.set_tracking(True)
    assert len(sock.sent) == 2


def test_tracking_already_set_does_not_write():
    sock = Socket(packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
                  + packet(1043, {"Ret": 100, "Detect.DetectTrack": {"Enable": 0}}, 1))
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            assert client.set_tracking(False) == {"enabled": False}
    assert len(sock.sent) == 2


def test_camera_clock_read_and_set_wire_format():
    from datetime import datetime, timezone, timedelta
    sock = Socket(packet(1001, {"Ret": 100, "SessionID": "0x00000001"})
                  + packet(1453, {"Ret": 100, "OPTimeQuery": "2026-10-26 07:39:06"}, 1)
                  + packet(1451, {"Ret": 100}, 2))
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            assert client.camera_time() == datetime(2026, 10, 26, 7, 39, 6)
            with pytest.raises(ProtocolError):
                client._exchange(1450, {"Name": "OPMachine", "OPMachine": {"Action": "Reboot"}})
            with pytest.raises(ProtocolError):
                client._exchange(1450, {"Name": "OPTimeSetting", "OPTimeSetting": "bad"})
            with pytest.raises(ProtocolError):
                client.set_camera_time(datetime(2026, 10, 26))
            client.set_camera_time(datetime(2026, 10, 26, 7, 41, 1, tzinfo=timezone(timedelta(hours=9))))
    assert [HEADER.unpack(p[:20])[4] for p in sock.sent] == [1000, 1452, 1450]
    assert json.loads(sock.sent[2][20:-2]) == {"Name": "OPTimeSetting", "SessionID": "0x00000001",
                                            "OPTimeSetting": "2026-10-26 07:41:01"}


@pytest.mark.parametrize("ret,error", [(106, AuthenticationError), (104, ProtocolError), (603, ProtocolError)])
def test_clock_failure_never_retries_or_reboots(ret, error):
    from datetime import datetime, timezone
    sock = Socket(packet(1001, {"Ret": 100, "SessionID": "0x00000001"}) + packet(1451, {"Ret": ret}, 1))
    with patch("socket.create_connection", return_value=sock):
        with DVRIPClient(Config(password="test-only")) as client:
            with pytest.raises(error):
                client.set_camera_time(datetime.now(timezone.utc))
    assert len(sock.sent) == 2
