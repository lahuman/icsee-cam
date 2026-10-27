"""Explicit browser authorities shared by HTTP and WebSocket guards."""
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from .config import Config


class WebAccess:
    def __init__(self,port,container_mode=False,lan_ip=None,public_origin=None):
        self.container_mode=container_mode
        self.lan_network=None
        self.hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        self.secure_hosts=set()
        self.public_origin=public_origin
        if public_origin:
            parsed=urlsplit(public_origin)
            if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or parsed.netloc!=parsed.netloc.lower()
                    or any(c in parsed.netloc for c in '*\\\r\n ')):
                raise ValueError('ICSEE_WEB_PUBLIC_ORIGIN must be an exact HTTPS origin without a path')
            self.hosts.add(parsed.netloc)
            self.secure_hosts.add(parsed.netloc)
        if lan_ip:
            Config(host=lan_ip)  # Literal RFC1918 IPv4 only.
            self.hosts.add(f'{lan_ip}:{port}')
            self.lan_network=ip_network(f'{lan_ip}/24',strict=False)

    def client_allowed(self,address):
        if not address:return False
        if self.container_mode:return True  # Published interface controls access.
        if address in ('127.0.0.1','::1'):return True
        try:return self.lan_network is not None and ip_address(address) in self.lan_network
        except ValueError:return False

    def origin_allowed(self,host,origin):
        scheme='https' if host in self.secure_hosts else 'http'
        return host in self.hosts and origin==f'{scheme}://{host}'

    def websocket_sources(self):
        return ' '.join(('wss://' if host in self.secure_hosts else 'ws://')+host for host in sorted(self.hosts))
