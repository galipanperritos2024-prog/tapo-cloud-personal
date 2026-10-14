"""Local network camera discovery.

Two complementary strategies:

1. **WS-Discovery** (ONVIF standard): a UDP multicast ``Probe`` to
   239.255.255.250:3702; ONVIF devices answer with their service address.
   Instant and passive for the network, but multicast does not cross
   routers/VLANs nor Docker bridge networks.
2. **Subnet scan** (opt-in): TCP connect to ports 554 (RTSP) and 2020
   (Tapo ONVIF) on the host's own /24 private subnets. Works everywhere
   unicast works. Restricted to RFC1918 ranges: this is a *home* tool, it
   must never be usable to scan the Internet.

Nothing here opens router ports or uses UPnP.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

WS_DISCOVERY_ADDR = ("239.255.255.250", 3702)

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:{msg_id}</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""


@dataclass
class DiscoveredDevice:
    ip: str
    onvif_port: int | None = None
    rtsp_port: int | None = None
    xaddrs: list[str] = field(default_factory=list)
    name: str | None = None
    hardware: str | None = None
    methods: list[str] = field(default_factory=list)
    likely_tapo: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _scope_value(scopes: str, key: str) -> str | None:
    m = re.search(rf"onvif://www\.onvif\.org/{key}/(\S+)", scopes)
    if not m:
        return None
    from urllib.parse import unquote

    return unquote(m.group(1)).replace("_", " ")


def parse_probe_match(data: bytes) -> DiscoveredDevice | None:
    s = data.decode(errors="replace")
    xm = re.search(r"<[^>]*XAddrs>([^<]+)</", s)
    if not xm:
        return None
    xaddrs = xm.group(1).split()
    host = urlsplit(xaddrs[0])
    if not host.hostname:
        return None
    scopes_m = re.search(r"<[^>]*Scopes[^>]*>([^<]+)</", s)
    scopes = scopes_m.group(1) if scopes_m else ""
    dev = DiscoveredDevice(
        ip=host.hostname,
        onvif_port=host.port or 80,
        xaddrs=xaddrs,
        name=_scope_value(scopes, "name"),
        hardware=_scope_value(scopes, "hardware"),
        methods=["ws-discovery"],
    )
    blob = f"{dev.name} {dev.hardware}".lower()
    dev.likely_tapo = "tapo" in blob or "tp-link" in blob or bool(re.search(r"\bc[1-5]\d{2}", blob))
    return dev


def ws_discovery(timeout_s: float = 3.0) -> list[DiscoveredDevice]:
    found: dict[str, DiscoveredDevice] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # never leaves the LAN
        sock.settimeout(0.5)
        sock.bind(("", 0))
        msg = _PROBE.format(msg_id=uuid.uuid4()).encode()
        for _ in range(2):  # UDP may drop; send twice
            sock.sendto(msg, WS_DISCOVERY_ADDR)
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            dev = parse_probe_match(data)
            if dev and dev.ip not in found:
                found[dev.ip] = dev
    except OSError as exc:
        log.warning("WS-Discovery unavailable: %s", exc)
    finally:
        sock.close()
    return list(found.values())


def local_private_networks() -> list[ipaddress.IPv4Network]:
    """The /24 networks of this host's private IPv4 addresses."""
    addrs: set[str] = set()
    try:
        # Route lookup trick: no packet is sent for UDP connect().
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    nets = set()
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            nets.add(ipaddress.ip_network(f"{a}/24", strict=False))
    return sorted(nets)


def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def subnet_scan(networks: list[ipaddress.IPv4Network] | None = None, timeout_s: float = 0.4) -> list[DiscoveredDevice]:
    networks = networks if networks is not None else local_private_networks()
    targets: list[str] = []
    for net in networks:
        if not net.is_private or net.num_addresses > 1024:
            log.warning("Refusing to scan %s (only private networks up to /22)", net)
            continue
        targets += [str(h) for h in net.hosts()]

    def check(ip: str) -> DiscoveredDevice | None:
        rtsp = _port_open(ip, 554, timeout_s)
        onvif = _port_open(ip, 2020, timeout_s)
        if not (rtsp or onvif):
            return None
        return DiscoveredDevice(
            ip=ip,
            rtsp_port=554 if rtsp else None,
            onvif_port=2020 if onvif else None,
            methods=["port-scan"],
            likely_tapo=rtsp and onvif,  # 554 + 2020 together is the Tapo signature
        )

    with ThreadPoolExecutor(max_workers=64) as pool:
        return [d for d in pool.map(check, targets) if d]


def configured_networks(spec: str | None) -> list[ipaddress.IPv4Network] | None:
    """Parse ``"192.168.1.0/24,10.0.0.0/24"``; ``None`` = auto-detect."""
    if not spec:
        return None
    return [ipaddress.ip_network(n.strip(), strict=False) for n in spec.split(",") if n.strip()]


def discover(scan_subnet: bool, networks: str | None = None) -> list[DiscoveredDevice]:
    by_ip = {d.ip: d for d in ws_discovery()}
    if scan_subnet:
        for d in subnet_scan(configured_networks(networks)):
            if d.ip in by_ip:
                cur = by_ip[d.ip]
                cur.rtsp_port = cur.rtsp_port or d.rtsp_port
                cur.methods.append("port-scan")
                cur.likely_tapo = cur.likely_tapo or d.likely_tapo
            else:
                by_ip[d.ip] = d
    return sorted(by_ip.values(), key=lambda d: tuple(int(x) for x in d.ip.split(".")) if d.ip.count(".") == 3 else (0,))
