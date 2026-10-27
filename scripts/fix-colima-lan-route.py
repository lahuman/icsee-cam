"""Repair Colima 0.10.1 hostAddresses' subnet-wide loopback route.

Run after Colima starts. Keep the exact host-IP route used by Docker publishing.
"""
import ipaddress
import json
import os
import subprocess

lan = ipaddress.IPv4Address(os.environ.get("ICSEE_WEB_LAN_IP", "192.168.1.159"))
if not lan.is_private or lan.is_loopback or lan.is_link_local:
    raise SystemExit("Expected a private LAN IPv4 address")
network = str(ipaddress.IPv4Network(f"{lan}/24", strict=False))
result = subprocess.run(
    ["colima", "ssh", "--", "ip", "-j", "route", "show", "table", "local"],
    check=True, capture_output=True, text=True,
)
for route in json.loads(result.stdout):
    if (route.get("dst") == network and route.get("dev") == "lo"
            and route.get("type") == "local"
            and route.get("prefsrc") == str(lan)):
        subprocess.run(
            ["colima", "ssh", "--", "sudo", "ip", "route", "del", "local",
             network, "dev", "lo", "table", "local"], check=True,
        )
        print(f"Removed Colima loopback subnet route: {network}")
        break
else:
    print("Colima LAN route needs no correction")
