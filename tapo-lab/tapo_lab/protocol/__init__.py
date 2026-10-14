"""EXPERIMENTAL — helpers to document protocol observations safely.

:func:`redact` scrubs anything that must never end up in shared research
notes: passwords, tokens, cookies, serials, MAC addresses and public IPs.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(?i)(pass|pwd|token|stok|secret|cookie|nonce|cnonce|digest|key|session|serial|sn$|device_id|hw_id|mac|ssid|bssid|email|account)"
)
_MAC = re.compile(r"\b([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _scrub_ip(m: re.Match) -> str:
    try:
        ip = ipaddress.ip_address(m.group(0))
    except ValueError:
        return m.group(0)
    return m.group(0) if ip.is_private else "<public-ip>"


def redact(obj: Any) -> Any:
    """Recursively redact a JSON-like structure (dict/list/str)."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _IP.sub(_scrub_ip, _MAC.sub("<mac>", obj))
    return obj


def to_note(feature: str, response: Any) -> str:
    """Markdown snippet ready to paste into research/<model>.md."""
    body = json.dumps(redact(response), indent=2, ensure_ascii=False, default=str)
    return f"### {feature}\n\n```json\n{body}\n```\n"
