"""System health snapshot for the dashboard and ``/api/system/status``."""

from __future__ import annotations

import shutil
import time
from typing import Any

import psutil
from sqlalchemy import func, select

from tapocloud import __version__
from tapocloud.config import get_settings
from tapocloud.core.db import session_scope
from tapocloud.core.ffmpeg import which_ffmpeg
from tapocloud.core.models import Camera, CameraStatus, Recording, UploadItem, UploadStatus

_BOOT = time.time()


def system_metrics() -> dict[str, Any]:
    s = get_settings()
    disk = shutil.disk_usage(s.storage_dir)
    vm = psutil.virtual_memory()
    return {
        "version": __version__,
        "uptime_seconds": int(time.time() - _BOOT),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "ram": {"total": vm.total, "used": vm.used, "percent": vm.percent},
        "disk": {"path": str(s.storage_dir), "total": disk.total, "used": disk.used, "free": disk.free},
        "binaries": which_ffmpeg(),
    }


def db_metrics() -> dict[str, Any]:
    with session_scope() as db:
        cams = dict(db.execute(select(Camera.status, func.count()).group_by(Camera.status)).all())
        queue = dict(db.execute(select(UploadItem.status, func.count()).group_by(UploadItem.status)).all())
        local_bytes = db.scalar(
            select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted_at.is_(None))
        )
        drive_bytes = db.scalar(
            select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.uploaded_at.is_not(None))
        )
        last_upload = db.scalar(select(func.max(UploadItem.completed_at)))
    return {
        "cameras": {
            "total": sum(cams.values()),
            "online": cams.get(CameraStatus.ONLINE.value, 0),
            "offline": cams.get(CameraStatus.OFFLINE.value, 0) + cams.get(CameraStatus.UNKNOWN.value, 0),
            "warning": cams.get(CameraStatus.WARNING.value, 0),
            "disabled": cams.get(CameraStatus.DISABLED.value, 0),
        },
        "upload_queue": {st.value: queue.get(st.value, 0) for st in UploadStatus},
        "storage": {"local_recordings_bytes": int(local_bytes or 0), "drive_bytes": int(drive_bytes or 0)},
        "last_upload_at": last_upload.isoformat() if last_upload else None,
    }
