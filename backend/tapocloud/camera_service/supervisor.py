"""Keeps one :class:`CameraWorker` per configured camera, in sync with the DB."""

from __future__ import annotations

import logging
import shutil
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tapocloud.camera_service.worker import CameraConfig, CameraWorker
from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.db import session_scope
from tapocloud.core.events import emit
from tapocloud.core.mediamtx import MediaMTXClient
from tapocloud.core.models import Camera, EventType

log = logging.getLogger(__name__)


class Supervisor(threading.Thread):
    RECONCILE_EVERY = 15.0

    def __init__(self) -> None:
        super().__init__(name="supervisor", daemon=True)
        self.mtx = MediaMTXClient()
        self.workers: dict[int, CameraWorker] = {}
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._wake = threading.Event()
        self._low_disk_alerted = False

    def reload(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    def get(self, camera_id: int) -> CameraWorker | None:
        with self._lock:
            return self.workers.get(camera_id)

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.reconcile()
                self._check_disk()
            except Exception:
                log.exception("Reconcile failed")
            self._wake.wait(self.RECONCILE_EVERY)
            self._wake.clear()
        with self._lock:
            workers = list(self.workers.values())
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=20)
        self.mtx.close()

    def reconcile(self) -> None:
        with session_scope() as db:
            rows = db.scalars(select(Camera).options(selectinload(Camera.credentials))).all()
            configs = {c.id: CameraConfig.from_row(c) for c in rows}
        with self._lock:
            for cam_id in list(self.workers):
                if cam_id not in configs:
                    log.info("Camera %s removed; stopping worker", cam_id)
                    w = self.workers.pop(cam_id)
                    w.update_config(_disabled(w.cfg))  # unregisters relay paths
                    w.stop()
            for cam_id, cfg in configs.items():
                w = self.workers.get(cam_id)
                if w is None or not w.is_alive():
                    w = CameraWorker(cfg, self.mtx)
                    self.workers[cam_id] = w
                    w.start()
                elif w.cfg != cfg:
                    w.update_config(cfg)

    def _check_disk(self) -> None:
        s = get_settings()
        free_gb = shutil.disk_usage(s.storage_dir).free / 1024**3
        with session_scope() as db:
            min_free = float(settings_store.get(db, "storage.min_free_gb"))
        if free_gb < min_free and not self._low_disk_alerted:
            self._low_disk_alerted = True
            emit(EventType.STORAGE_ERROR, message=f"Espacio local bajo: {free_gb:.1f} GB libres (mínimo {min_free} GB)")
        elif free_gb >= min_free * 1.2:
            self._low_disk_alerted = False

    def status(self) -> dict:
        with self._lock:
            workers = list(self.workers.values())
        return {
            "mediamtx": self.mtx.healthy(),
            "cameras": [w.summary() for w in workers],
            "time": time.time(),
        }


def _disabled(cfg: CameraConfig) -> CameraConfig:
    from dataclasses import replace

    return replace(cfg, enabled=False)
