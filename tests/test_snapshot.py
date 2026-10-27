from unittest.mock import Mock,patch
import pytest
from icsee_ptz.config import AuthenticationError,Config,ProtocolError
from icsee_ptz.snapshot import SnapshotClient


def client(data):
    c=object.__new__(SnapshotClient)
    c.config=Config(password='private-value')
    c.url='http://192.168.1.168/snapshot'
    response=Mock()
    response.__enter__=Mock(return_value=response)
    response.__exit__=Mock(return_value=False)
    response.iter_content.return_value=iter([data])
    c.session=Mock();c.session.get.return_value=response
    return c


def test_missing_password_cannot_connect():
    with patch('icsee_ptz.snapshot.ONVIFProbe') as probe:
        with pytest.raises(AuthenticationError):SnapshotClient(Config())
        probe.assert_not_called()


def test_only_jpeg_success_is_returned():
    c=client(b'\xff\xd8image\xff\xd9')
    assert c.capture()==b'\xff\xd8image\xff\xd9'
    c.session.get.assert_called_once_with(c.url,timeout=3,stream=True)


@pytest.mark.parametrize('data',[b'<html>login page</html>',b'\xff\xd8truncated',b'x'*(8*1024*1024+1)])
def test_invalid_or_oversize_snapshot_not_saved(data):
    with pytest.raises(ProtocolError):client(data).capture()
