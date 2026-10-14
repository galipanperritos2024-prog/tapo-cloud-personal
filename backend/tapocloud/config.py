"""Application configuration.

All settings can be overridden with environment variables prefixed ``TAPOCLOUD_``
(or a ``.env`` file). Values that the user changes from the UI at runtime
(recording defaults, retention, Drive options...) live in the ``settings`` table
instead; this module only holds *deployment* configuration.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Per-OS default data directory (Windows: %LOCALAPPDATA%\\TapoCloud)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "TapoCloud"
    return Path.home() / ".local" / "share" / "tapocloud"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAPOCLOUD_", env_file=".env", extra="ignore")

    # --- Paths -------------------------------------------------------------
    data_dir: Path = Field(default_factory=_default_data_dir)
    # Recordings can live on a different (bigger) disk than the database.
    recordings_dir: Path | None = None
    # Directory with the exported frontend (Next.js `out/`). Optional.
    frontend_dir: Path | None = None

    # --- Database ----------------------------------------------------------
    # SQLite by default; any SQLAlchemy URL works (e.g. postgresql+psycopg://...)
    database_url: str | None = None

    # --- Network -----------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Proxies allowed to set X-Forwarded-* headers (comma separated, "*" = any).
    forwarded_allow_ips: str = "127.0.0.1"
    camera_service_host: str = "127.0.0.1"
    camera_service_port: int = 8001
    # Shared secret between backend <-> camera-service <-> MediaMTX.
    # Generated automatically on first run if not provided.
    internal_token: str | None = None

    # --- MediaMTX (streaming relay) ---------------------------------------
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    mediamtx_rtsp_url: str = "rtsp://127.0.0.1:8554"
    # URLs as seen *by the browser*. "{host}" is replaced with the host the
    # user used to open the UI, so it works from any device on the LAN.
    # A path such as "/webrtc" means "same origin, behind the reverse proxy".
    public_webrtc_url: str = "http://{host}:8889"
    public_hls_url: str = "http://{host}:8888"
    # Networks for the optional subnet scan, e.g. "192.168.1.0/24". Needed in
    # Docker, where the container only sees its own bridge network.
    scan_networks: str | None = None

    # --- Binaries ----------------------------------------------------------
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # --- Security ------------------------------------------------------------
    # Master key for credential encryption (Fernet, urlsafe base64, 32 bytes).
    # If unset, it is read from / generated into <data_dir>/secret.key.
    master_key: str | None = None
    session_hours: int = 24 * 7
    # Set to true when serving over HTTPS (reverse proxy / Tailscale serve).
    secure_cookies: bool = False
    # Refuse cameras with public IPs (defence against SSRF-style misuse).
    allow_public_camera_ips: bool = False

    # --- Google OAuth --------------------------------------------------------
    # Can also be configured from the UI (stored encrypted in the DB).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/google/callback"

    # --- Experimental ------------------------------------------------------
    tapo_lab_enabled: bool = False

    log_level: str = "INFO"

    # ----------------------------------------------------------------------
    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'tapocloud.db').as_posix()}"

    @property
    def storage_dir(self) -> Path:
        return self.recordings_dir or (self.data_dir / "recordings")

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def work_dir(self) -> Path:
        """Scratch area: pre-event ring buffers, in-progress segments."""
        return self.data_dir / "work"

    @property
    def camera_service_url(self) -> str:
        return f"http://{self.camera_service_host}:{self.camera_service_port}"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.storage_dir, self.thumbs_dir, self.logs_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
