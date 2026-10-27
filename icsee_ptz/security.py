"""Client identity, bounded request budgets and server-side permissions."""
from collections import OrderedDict, deque
from ipaddress import ip_address, ip_network
import os
import threading
import time

from .config import CameraError


def positive_env(name, default, maximum=100000):
    try:
        value = int(os.environ.get(name, default))
        if not 1 <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise CameraError(f'{name} must be an integer from 1 to {maximum}.') from None


class ClientIdentity:
    def __init__(self, trusted_proxies=None):
        value = os.environ.get('ICSEE_TRUSTED_PROXIES', '') if trusted_proxies is None else trusted_proxies
        try:
            self.networks = [ip_network(item.strip(), strict=False) for item in value.split(',') if item.strip()]
            if any(net.prefixlen == 0 for net in self.networks):
                raise ValueError
        except ValueError:
            raise CameraError('ICSEE_TRUSTED_PROXIES must contain explicit proxy IPs/CIDRs, not a wildcard.') from None

    def trusted(self, address):
        return any(address in net for net in self.networks)

    def identify(self, connection):
        peer = connection.client.host if connection.client else 'unknown'
        try:
            address = ip_address(peer)
        except ValueError:
            return peer
        if not self.trusted(address):
            return str(address)
        forwarded = connection.headers.get('x-forwarded-for', '')
        if not forwarded:
            return str(address)
        if len(forwarded) > 1024:
            raise CameraError('Invalid proxy client address.')
        try:
            chain = [ip_address(item.strip()) for item in forwarded.split(',')]
        except ValueError:
            raise CameraError('Invalid proxy client address.') from None
        if not 1 <= len(chain) <= 16:
            raise CameraError('Invalid proxy client address.')
        # Work from our trusted peer towards the first untrusted hop. A client
        # cannot pick an arbitrary leftmost XFF value to escape its budget.
        for hop in reversed(chain):
            address = hop
            if not self.trusted(hop):
                break
        return str(address)


class RequestLimits:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.buckets = OrderedDict()
        self.capacity = 10000
        self.limits = {
            'login_ip': positive_env('ICSEE_LOGIN_IP_LIMIT', 20),
            'login_account_ip': positive_env('ICSEE_LOGIN_ACCOUNT_IP_LIMIT', 10),
            'read': positive_env('ICSEE_READ_LIMIT', 240),
            'control': positive_env('ICSEE_CONTROL_LIMIT', 60),
            'job': positive_env('ICSEE_JOB_LIMIT', 10),
            'connect': positive_env('ICSEE_CONNECT_LIMIT', 30),
            'read_session': positive_env('ICSEE_READ_SESSION_LIMIT', 120),
            'control_session': positive_env('ICSEE_CONTROL_SESSION_LIMIT', 30),
            'job_session': positive_env('ICSEE_JOB_SESSION_LIMIT', 10),
            'connect_session': positive_env('ICSEE_CONNECT_SESSION_LIMIT', 15),
        }

    def allow(self, category, key):
        now = self.clock()
        with self.lock:
            while self.buckets:
                first, entries = next(iter(self.buckets.items()))
                if entries and entries[-1] > now - 60:
                    break
                self.buckets.pop(first)
            bucket_key = (category, key)
            if bucket_key not in self.buckets and len(self.buckets) >= self.capacity:
                return False
            bucket = self.buckets.setdefault(bucket_key, deque())
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            self.buckets.move_to_end(bucket_key)
            if len(bucket) >= self.limits[category]:
                return False
            bucket.append(now)
            return True


ROLES = {'viewer': 0, 'operator': 1, 'admin': 2}


def required_role(path, method):
    if path.startswith('/api/admin/'):
        return 'admin'
    if path == '/audio/talk':
        return 'operator'
    if method == 'POST' and path not in ('/api/logout', '/api/login', '/api/sd/search', '/api/check'):
        return 'operator'
    return 'viewer'


def request_category(path, method, websocket=False):
    if path in ('/api/stop', '/api/logout', '/api/admin/presets/stop'):
        return None  # Authentication and authorization still apply.
    if websocket or path == '/video' or path.endswith('/video') or path.endswith('.mp4'):
        return 'connect'
    if path.endswith('/render') or path == '/api/sd/preview':
        return 'job'
    return 'control' if method == 'POST' else 'read'
