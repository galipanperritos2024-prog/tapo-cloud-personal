"""Request/response schemas. All user input is validated here.

Camera credentials only ever travel *into* the API: no response schema has a
password field, and usernames are masked.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tapocloud.camera_service import schedule as schedule_mod
from tapocloud.config import get_settings
from tapocloud.core.models import MotionSource, RecordingMode

_STREAM_PATH = re.compile(r"^[A-Za-z0-9_\-./=?&]{1,128}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
SEGMENT_CHOICES = (1, 5, 10, 15, 30, 60)


def validate_camera_host(value: str) -> str:
    value = value.strip()
    try:
        ip = ipaddress.ip_address(value)
        addrs = [ip]
    except ValueError:
        if not _HOSTNAME.match(value):
            raise ValueError("IP o nombre de host inválido") from None
        try:
            addrs = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(value, None)]
        except OSError:
            raise ValueError("No se puede resolver el nombre de host") from None
    if not get_settings().allow_public_camera_ips:
        for a in addrs:
            if not (a.is_private or a.is_link_local) or a.is_loopback or a.is_multicast:
                raise ValueError("Solo se permiten cámaras en la red local (IP privada)")
    return value


class CameraBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=48)
    ip: str
    rtsp_port: int = Field(554, ge=1, le=65535)
    onvif_port: int = Field(2020, ge=1, le=65535)
    main_stream_path: str = "stream1"
    sub_stream_path: str | None = "stream2"

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        return validate_camera_host(v)

    @field_validator("main_stream_path", "sub_stream_path")
    @classmethod
    def _path(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        v = v.lstrip("/")
        if not _STREAM_PATH.match(v) or ".." in v:
            raise ValueError("Ruta de stream inválida")
        return v


class CameraCredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RecordingConfig(BaseModel):
    recording_mode: RecordingMode = RecordingMode.MANUAL
    auto_record: bool = True
    segment_minutes: int = 5
    pre_event_seconds: int = Field(10, ge=0, le=30)
    post_event_seconds: int = Field(30, ge=10, le=120)
    motion_source: MotionSource = MotionSource.BOTH
    motion_sensitivity: int = Field(50, ge=1, le=100)
    record_audio: bool = True
    schedule: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("segment_minutes")
    @classmethod
    def _seg(cls, v: int) -> int:
        if v not in SEGMENT_CHOICES:
            raise ValueError(f"Duración de segmento debe ser una de {SEGMENT_CHOICES}")
        return v

    @field_validator("schedule")
    @classmethod
    def _sched(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            return schedule_mod.validate(v)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"Horario inválido: {exc}") from exc


class CameraCreate(CameraBase, RecordingConfig):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    onvif_enabled: bool = True


class CameraTest(CameraBase):
    name: str = "test"
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    onvif: bool = True


class CameraUpdate(BaseModel):
    """Partial update: every field optional."""

    name: str | None = Field(None, min_length=1, max_length=48)
    ip: str | None = None
    rtsp_port: int | None = Field(None, ge=1, le=65535)
    onvif_port: int | None = Field(None, ge=1, le=65535)
    main_stream_path: str | None = None
    sub_stream_path: str | None = None
    enabled: bool | None = None
    onvif_enabled: bool | None = None
    username: str | None = Field(None, min_length=1, max_length=64)
    password: str | None = Field(None, min_length=1, max_length=128)
    recording_mode: RecordingMode | None = None
    auto_record: bool | None = None
    segment_minutes: int | None = None
    pre_event_seconds: int | None = Field(None, ge=0, le=30)
    post_event_seconds: int | None = Field(None, ge=10, le=120)
    motion_source: MotionSource | None = None
    motion_sensitivity: int | None = Field(None, ge=1, le=100)
    record_audio: bool | None = None
    schedule: list[dict[str, Any]] | None = None

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        return validate_camera_host(v) if v else v

    @field_validator("main_stream_path", "sub_stream_path")
    @classmethod
    def _path(cls, v: str | None) -> str | None:
        return CameraBase._path(v)

    @field_validator("segment_minutes")
    @classmethod
    def _seg(cls, v: int | None) -> int | None:
        return v if v is None else RecordingConfig._seg(v)

    @field_validator("schedule")
    @classmethod
    def _sched(cls, v: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        return v if v is None else RecordingConfig._sched(v)


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    ip: str
    rtsp_port: int
    onvif_port: int
    main_stream_path: str
    sub_stream_path: str | None
    enabled: bool
    manufacturer: str | None
    model: str | None
    firmware: str | None
    onvif_enabled: bool
    ptz_enabled: bool
    ptz_caps: dict[str, Any]
    recording_mode: str
    auto_record: bool
    segment_minutes: int
    pre_event_seconds: int
    post_event_seconds: int
    motion_source: str
    motion_sensitivity: int
    record_audio: bool
    schedule: list[dict[str, Any]]
    status: str
    status_detail: str | None
    last_seen_at: datetime | None
    stream_info: dict[str, Any]
    sub_stream_info: dict[str, Any]
    created_at: datetime
    username_masked: str | None = None
    rtsp_url_masked: str | None = None


class PtzCommand(BaseModel):
    action: Literal["move", "stop", "goto_preset", "set_preset"]
    pan: float = Field(0, ge=-1, le=1)
    tilt: float = Field(0, ge=-1, le=1)
    zoom: float = Field(0, ge=-1, le=1)
    duration: float = Field(0.6, ge=0.1, le=5)
    preset: str | None = Field(None, max_length=64)
    preset_name: str | None = Field(None, max_length=32)


class RecordAction(BaseModel):
    action: Literal["start", "stop"]
    max_minutes: int | None = Field(None, ge=1, le=240)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=10, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class GoogleConfigIn(BaseModel):
    client_id: str = Field(min_length=10, max_length=256, pattern=r"^[A-Za-z0-9._-]+$")
    client_secret: str | None = Field(None, max_length=256)
    redirect_uri: str | None = Field(None, max_length=256, pattern=r"^https?://")


class DiscoveryIn(BaseModel):
    subnet_scan: bool = False
