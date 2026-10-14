"""ORM models (the data model of the application).

Design notes
------------
* Secrets never live next to regular columns: camera passwords are in
  ``camera_credentials`` (Fernet-encrypted), Google tokens in ``settings``
  rows flagged ``encrypted``. API serializers never touch those tables.
* Every timestamp is stored in UTC; the UI converts to local time.
* Status columns are plain strings (validated by enums in code) so that new
  states can be added without a migration.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapocloud.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """DateTime that always round-trips as timezone-aware UTC (SQLite drops tz)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# --------------------------------------------------------------------------
# Enums (stored as strings)
# --------------------------------------------------------------------------
class CameraStatus(enum.StrEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    WARNING = "warning"  # reachable but degraded (e.g. sub-stream missing)
    DISABLED = "disabled"


class RecordingMode(enum.StrEnum):
    CONTINUOUS = "continuous"  # 24/7 segments
    MOTION = "motion"  # pre/post buffered clips on motion
    EVENT = "event"  # pre/post buffered clips on smart events (person, vehicle...)
    MANUAL = "manual"  # only when the user presses "record"
    SCHEDULE = "schedule"  # continuous inside the configured time windows


class MotionSource(enum.StrEnum):
    ONVIF = "onvif"  # Level 1: camera's own detection via ONVIF events
    LOCAL = "local"  # Level 2: our own frame analysis
    BOTH = "both"
    OFF = "off"  # no detection at all


class RecordingKind(enum.StrEnum):
    CONTINUOUS = "continuous"
    EVENT = "event"
    MANUAL = "manual"


class RecordingStatus(enum.StrEnum):
    RECORDING = "recording"
    COMPLETE = "complete"
    FAILED = "failed"


class EventType(enum.StrEnum):
    MOTION = "motion"
    PERSON = "person"
    VEHICLE = "vehicle"
    ANIMAL = "animal"
    CAMERA_OFFLINE = "camera_offline"
    CAMERA_ONLINE = "camera_online"
    RECORDING_STARTED = "recording_started"
    RECORDING_FINISHED = "recording_finished"
    STORAGE_ERROR = "storage_error"
    UPLOAD_ERROR = "upload_error"


class UploadStatus(enum.StrEnum):
    PENDING = "pending"  # esperando
    UPLOADING = "uploading"  # subiendo
    COMPLETED = "completed"  # completado
    ERROR = "error"  # error (permanent, needs user action)
    RETRY = "retry"  # reintento (transient failure, scheduled)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserSession(Base):
    """Server-side session. Only a SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # Filesystem / Drive-safe name derived from ``name`` ("Salón" -> "Salon").
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    ip: Mapped[str] = mapped_column(String(64))
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    onvif_port: Mapped[int] = mapped_column(Integer, default=2020)  # Tapo default
    main_stream_path: Mapped[str] = mapped_column(String(128), default="stream1")
    sub_stream_path: Mapped[str | None] = mapped_column(String(128), default="stream2")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Device info (from ONVIF when available)
    manufacturer: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    firmware: Mapped[str | None] = mapped_column(String(64))
    onvif_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ptz_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"pan_tilt": bool, "zoom": bool, "presets": bool, "profile_token": "..."}
    ptz_caps: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Recording configuration
    recording_mode: Mapped[str] = mapped_column(String(16), default=RecordingMode.MANUAL.value)
    auto_record: Mapped[bool] = mapped_column(Boolean, default=True)
    segment_minutes: Mapped[int] = mapped_column(Integer, default=5)
    pre_event_seconds: Mapped[int] = mapped_column(Integer, default=10)
    post_event_seconds: Mapped[int] = mapped_column(Integer, default=30)
    motion_source: Mapped[str] = mapped_column(String(8), default=MotionSource.BOTH.value)
    motion_sensitivity: Mapped[int] = mapped_column(Integer, default=50)  # 1..100
    record_audio: Mapped[bool] = mapped_column(Boolean, default=True)
    # [{"days": [0,1,2,3,4], "start": "22:00", "end": "08:00"}, ...]  (0 = Monday)
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Runtime state (written by camera-service)
    status: Mapped[str] = mapped_column(String(16), default=CameraStatus.UNKNOWN.value)
    status_detail: Mapped[str | None] = mapped_column(String(255))
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # {"codec": "h264", "width": 2304, "height": 1296, "fps": 15, "bitrate_kbps": 2100, "audio": "pcm_alaw"}
    stream_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sub_stream_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    credentials: Mapped[CameraCredentials | None] = relationship(
        back_populates="camera", cascade="all, delete-orphan", uselist=False
    )


class CameraCredentials(Base):
    """RTSP/ONVIF credentials (the Tapo "camera account", not the TP-Link cloud account)."""

    __tablename__ = "camera_credentials"

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    password_encrypted: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    camera: Mapped[Camera] = relationship(back_populates="credentials")


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (Index("ix_recordings_camera_start", "camera_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL: deleting a camera must never orphan pending uploads or drop
    # evidence; its recordings stay listed, uploadable and subject to retention.
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=RecordingStatus.RECORDING.value)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    # Path relative to the recordings directory: "Salon/2026/10/14/23-00-00.mp4"
    file_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    local_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    drive_file_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    camera: Mapped[Camera] = relationship()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_camera_time", "camera_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str | None] = mapped_column(String(16))  # onvif | local | system
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    recording_id: Mapped[int | None] = mapped_column(ForeignKey("recordings.id", ondelete="SET NULL"))
    message: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class UploadItem(Base):
    __tablename__ = "upload_queue"
    __table_args__ = (Index("ix_upload_status_next", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=UploadStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(512))
    # Google resumable session URI: lets an interrupted upload continue where it stopped.
    session_uri: Mapped[str | None] = mapped_column(Text)
    bytes_uploaded: Mapped[int] = mapped_column(BigInteger, default=0)
    drive_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    recording: Mapped[Recording] = relationship()


class DriveFolder(Base):
    """Cache of Drive folder IDs by logical path ("TapoCloud/Salon/2026/10/14")."""

    __tablename__ = "drive_folders"

    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    folder_id: Mapped[str] = mapped_column(String(128))
    account: Mapped[str | None] = mapped_column(String(255))


class Setting(Base):
    """Key/value runtime settings. ``encrypted`` rows hold Fernet tokens."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
