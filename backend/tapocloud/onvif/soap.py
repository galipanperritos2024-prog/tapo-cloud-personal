"""SOAP transport with WS-Security UsernameToken digest authentication.

``PasswordDigest = Base64(SHA1(nonce + created + password))``

``created`` must be close to the *camera's* clock, otherwise the camera
rejects the request ("NotAuthorized"). Tapo cameras without NTP access often
drift, so :class:`SoapClient` reads ``GetSystemDateAndTime`` (which needs no
authentication) and applies the measured offset to every request.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

import httpx
from defusedxml.ElementTree import fromstring as safe_fromstring

log = logging.getLogger(__name__)

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wsa": "http://www.w3.org/2005/08/addressing",
}

_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_PWD_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
_B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"


class OnvifError(RuntimeError):
    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth


# --------------------------------------------------------------------------
# XML helpers (namespace-agnostic lookups keep parsing robust across firmwares)
# --------------------------------------------------------------------------
def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_all(el: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in el.iter() if local(e.tag) == name]


def find(el: ET.Element, name: str) -> ET.Element | None:
    return next((e for e in el.iter() if local(e.tag) == name), None)


def text(el: ET.Element | None, name: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    node = find(el, name)
    return node.text.strip() if node is not None and node.text else default


def password_digest(password: str, nonce: bytes, created: str) -> str:
    return base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()


def security_header(username: str, password: str, now: datetime) -> str:
    nonce = os.urandom(16)
    created = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return (
        f'<wsse:Security s:mustUnderstand="1" xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{escape(username)}</wsse:Username>"
        f'<wsse:Password Type="{_PWD_DIGEST}">{password_digest(password, nonce, created)}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{_B64}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def envelope(body: str, header: str = "") -> str:
    ns_decl = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope {ns_decl}><s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>'


def parse_fault(root: ET.Element) -> str | None:
    fault = find(root, "Fault")
    if fault is None:
        return None
    reason = text(fault, "Text") or text(fault, "faultstring") or "SOAP fault"
    subcode = " ".join(e.text or "" for e in find_all(fault, "Value"))
    return f"{reason} {subcode}".strip()


# --------------------------------------------------------------------------
class SoapClient:
    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 8.0) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.device_url = f"http://{host}:{port}/onvif/device_service"
        self.clock_offset = timedelta(0)
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SoapClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def sync_clock(self) -> None:
        """Measure camera clock offset (unauthenticated call)."""
        try:
            root = self.call(self.device_url, "<tds:GetSystemDateAndTime/>", auth=False)
        except OnvifError:
            return
        utc = find(root, "UTCDateTime")
        if utc is None:
            return
        try:
            d, t = find(utc, "Date"), find(utc, "Time")
            cam = datetime(
                int(text(d, "Year")), int(text(d, "Month")), int(text(d, "Day")),
                int(text(t, "Hour")), int(text(t, "Minute")), int(text(t, "Second")), tzinfo=UTC,
            )  # fmt: skip
            self.clock_offset = cam - datetime.now(UTC)
            if abs(self.clock_offset.total_seconds()) > 5:
                log.info("Camera %s clock offset %.0fs (compensated)", self.host, self.clock_offset.total_seconds())
        except (TypeError, ValueError):
            pass

    def call(self, url: str, body: str, *, auth: bool = True, timeout: float | None = None) -> ET.Element:
        header = security_header(self.username, self.password, datetime.now(UTC) + self.clock_offset) if auth else ""
        try:
            resp = self._http.post(
                url,
                content=envelope(body, header).encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=timeout or self._http.timeout,
            )
        except httpx.HTTPError as exc:
            hint = " (la cámara no responde en esa IP/puerto)" if isinstance(exc, httpx.ConnectError | httpx.TimeoutException) else ""
            raise OnvifError(f"ONVIF request failed: {exc.__class__.__name__}{hint}") from exc
        try:
            root = safe_fromstring(resp.content)  # hardened against XML bombs / external entities
        except ET.ParseError as exc:
            raise OnvifError(f"Invalid ONVIF response (HTTP {resp.status_code})") from exc
        fault = parse_fault(root)
        if fault or resp.status_code >= 400:
            msg = fault or f"HTTP {resp.status_code}"
            is_auth = resp.status_code == 401 or "NotAuthorized" in msg or "authority" in msg.lower()
            raise OnvifError(msg, auth=is_auth)
        return root
