"""Filesystem/Drive-safe naming and the canonical recording layout.

Layout (identical locally and in Google Drive)::

    <root>/<CameraSlug>/<YYYY>/<MM>/<DD>/<HH-MM-SS>.mp4          continuous
    <root>/<CameraSlug>/<YYYY>/<MM>/<DD>/<HH-MM-SS>_event.mp4    event clip
    <root>/<CameraSlug>/<YYYY>/<MM>/<DD>/<HH-MM-SS>_manual.mp4   manual

Dates use the *local* time of the machine, because that is what a person
browsing Drive expects ("the 23:00 clip"); the DB keeps UTC.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def slugify(name: str, max_len: int = 48) -> str:
    """Make a name safe for folders: ``Salón principal`` -> ``Salon_principal``."""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    norm = re.sub(r"[^A-Za-z0-9]+", "_", norm).strip("_")[:max_len] or "camera"
    if norm.upper() in _WINDOWS_RESERVED:
        norm = f"{norm}_cam"
    return norm


def recording_relpath(camera_slug: str, local_start: datetime, suffix: str = "") -> PurePosixPath:
    """Relative path of a recording, e.g. ``Salon/2026/10/14/23-00-00.mp4``."""
    fname = local_start.strftime("%H-%M-%S") + (f"_{suffix}" if suffix else "") + ".mp4"
    return PurePosixPath(camera_slug, local_start.strftime("%Y"), local_start.strftime("%m"), local_start.strftime("%d"), fname)


def safe_join(root, rel: str):
    """Join ``rel`` under ``root`` refusing path traversal (``..``, absolute paths)."""
    from pathlib import Path

    root_p = Path(root).resolve()
    target = (root_p / rel).resolve()
    if root_p != target and root_p not in target.parents:
        raise ValueError("Path escapes storage root")
    return target
