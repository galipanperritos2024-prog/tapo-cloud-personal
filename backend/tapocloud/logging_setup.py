"""Logging configuration with automatic secret redaction.

Every log record passes through :class:`RedactingFilter`, which masks
credentials embedded in RTSP/HTTP URLs and common ``password=``/``token=``
patterns. This is defence in depth: code should never log secrets in the
first place, but FFmpeg error lines, for example, can echo the input URL.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_URL_CREDS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@")
_KV_SECRETS = re.compile(
    r"(?i)(?P<key>password|passwd|pwd|secret|token|access_token|refresh_token|client_secret)"
    r"(?P<sep>[\"']?\s*[:=]\s*[\"']?)(?P<val>[^\s\"'&,}]+)"
)


def redact(text: str) -> str:
    """Mask credentials in an arbitrary string."""
    text = _URL_CREDS.sub(lambda m: f"{m['scheme']}{m['user']}:***@", text)
    return _KV_SECRETS.sub(lambda m: f"{m['key']}{m['sep']}***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, None
        return True


def setup_logging(level: str, logs_dir: Path, name: str) -> None:
    """Configure console + rotating file logging for a process.

    Format matches the UI log viewer: ``[23:04:12] INFO  camera.salon: connected``.
    """
    root = logging.getLogger()
    if getattr(root, "_tapocloud_configured", False):
        return
    root.setLevel(level.upper())
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    file_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_h = logging.handlers.RotatingFileHandler(
        logs_dir / f"{name}.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(file_fmt)
    redactor = RedactingFilter()
    for h in (console, file_h):
        h.addFilter(redactor)
        root.addHandler(h)
    # Quiet noisy libraries.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._tapocloud_configured = True  # type: ignore[attr-defined]
