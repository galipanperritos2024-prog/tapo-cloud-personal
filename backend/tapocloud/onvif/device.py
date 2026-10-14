"""ONVIF device operations: info, media profiles, stream URIs, PTZ and PullPoint events.

Tapo specifics (C100/C200/C210/C220/C310/C320WS/C500/C520WS, firmware with ONVIF):
* ONVIF service on port **2020**, same credentials as RTSP (camera account).
* Two media profiles: ``profile_1`` (main, ``stream1``) and ``profile_2`` (sub).
* PTZ models support ContinuousMove/Stop and presets; no optical zoom.
* Events: ``tns1:RuleEngine/CellMotionDetector/Motion`` and, on newer
  firmwares, person/vehicle/pet detector topics.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

from tapocloud.onvif.soap import OnvifError, SoapClient, find, find_all, local, text

log = logging.getLogger(__name__)


@dataclass
class MediaProfile:
    token: str
    name: str
    encoding: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate_kbps: int | None = None
    has_ptz: bool = False
    stream_uri: str | None = None


@dataclass
class DeviceInfo:
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    hardware_id: str | None = None
    media_xaddr: str | None = None
    ptz_xaddr: str | None = None
    events_xaddr: str | None = None
    profiles: list[MediaProfile] = field(default_factory=list)
    ptz: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["profiles"] = [p.__dict__ for p in self.profiles]
        return d


@dataclass
class Notification:
    topic: str
    items: dict[str, str]
    utc_time: datetime | None


class OnvifCamera:
    """High-level wrapper. Create, call :meth:`connect`, then use; close when done."""

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self.host, self.port = host, port
        self.soap = SoapClient(host, port, username, password)
        self.info = DeviceInfo()

    def close(self) -> None:
        self.soap.close()

    def __enter__(self) -> OnvifCamera:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    def _fix_xaddr(self, url: str | None) -> str | None:
        """Cameras sometimes report an unreachable host (other NIC, NAT, Docker).

        We keep the path but always talk to the host/port we connected to.
        """
        if not url:
            return None
        parts = urlsplit(url)
        return urlunsplit((parts.scheme or "http", f"{self.host}:{self.port}", parts.path, parts.query, ""))

    def connect(self, with_streams: bool = True) -> DeviceInfo:
        self.soap.sync_clock()
        caps = self.soap.call(self.soap.device_url, "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>")
        for section, attr in (("Media", "media_xaddr"), ("PTZ", "ptz_xaddr"), ("Events", "events_xaddr")):
            node = find(caps, section)
            setattr(self.info, attr, self._fix_xaddr(text(node, "XAddr")))

        try:
            di = self.soap.call(self.soap.device_url, "<tds:GetDeviceInformation/>")
            self.info.manufacturer = text(di, "Manufacturer")
            self.info.model = text(di, "Model")
            self.info.firmware = text(di, "FirmwareVersion")
            self.info.serial = text(di, "SerialNumber")
            self.info.hardware_id = text(di, "HardwareId")
        except OnvifError as exc:
            if exc.auth:
                raise
            log.debug("GetDeviceInformation failed: %s", exc)

        self.info.profiles = self.get_profiles()
        if with_streams:
            for p in self.info.profiles:
                try:
                    p.stream_uri = self.get_stream_uri(p.token)
                except OnvifError as exc:
                    log.debug("GetStreamUri(%s) failed: %s", p.token, exc)
        if self.info.ptz_xaddr and any(p.has_ptz for p in self.info.profiles):
            self.info.ptz = self.get_ptz_capabilities()
        return self.info

    # -- media -----------------------------------------------------------
    def get_profiles(self) -> list[MediaProfile]:
        if not self.info.media_xaddr:
            return []
        root = self.soap.call(self.info.media_xaddr, "<trt:GetProfiles/>")
        profiles = []
        for p in find_all(root, "Profiles"):
            token = p.get("token") or ""
            vec = find(p, "VideoEncoderConfiguration")
            res = find(vec, "Resolution") if vec is not None else None
            rate = find(vec, "RateControl") if vec is not None else None
            prof = MediaProfile(token=token, name=text(p, "Name") or token)
            if vec is not None:
                prof.encoding = text(vec, "Encoding")
            if res is not None:
                prof.width = _int(text(res, "Width"))
                prof.height = _int(text(res, "Height"))
            if rate is not None:
                prof.fps = _float(text(rate, "FrameRateLimit"))
                prof.bitrate_kbps = _int(text(rate, "BitrateLimit"))
            prof.has_ptz = find(p, "PTZConfiguration") is not None
            profiles.append(prof)
        return profiles

    def get_stream_uri(self, profile_token: str) -> str | None:
        body = (
            "<trt:GetStreamUri><trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            f"</trt:StreamSetup><trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken></trt:GetStreamUri>"
        )
        return text(self.soap.call(self.info.media_xaddr or self.soap.device_url, body), "Uri")

    # -- PTZ -------------------------------------------------------------
    def _ptz_profile(self) -> str:
        for p in self.info.profiles:
            if p.has_ptz:
                return p.token
        if self.info.profiles:
            return self.info.profiles[0].token
        raise OnvifError("No media profile available for PTZ")

    def get_ptz_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {"pan_tilt": False, "zoom": False, "presets": False, "profile_token": self._ptz_profile()}
        try:
            root = self.soap.call(self.info.ptz_xaddr, "<tptz:GetNodes/>")
        except OnvifError:
            caps["pan_tilt"] = True  # node listing unsupported: assume basic pan/tilt
            return caps
        for node in find_all(root, "PTZNode"):
            spaces = find(node, "SupportedPTZSpaces")
            if spaces is not None:
                names = {local(e.tag) for e in spaces}
                caps["pan_tilt"] |= "ContinuousPanTiltVelocitySpace" in names
                caps["zoom"] |= "ContinuousZoomVelocitySpace" in names
            max_presets = _int(text(node, "MaximumNumberOfPresets"))
            caps["presets"] |= bool(max_presets)
        return caps

    def continuous_move(self, pan: float, tilt: float, zoom: float = 0.0, timeout_s: float = 0.6) -> None:
        """Move at the given velocity (-1..1). The camera stops itself after ``timeout_s``."""
        token = self._ptz_profile()
        vel = ""
        if pan or tilt:
            vel += f'<tt:PanTilt x="{_clamp(pan)}" y="{_clamp(tilt)}"/>'
        if zoom:
            vel += f'<tt:Zoom x="{_clamp(zoom)}"/>'
        body = (
            f"<tptz:ContinuousMove><tptz:ProfileToken>{escape(token)}</tptz:ProfileToken>"
            f"<tptz:Velocity>{vel}</tptz:Velocity><tptz:Timeout>PT{timeout_s:.1f}S</tptz:Timeout></tptz:ContinuousMove>"
        )
        self.soap.call(self.info.ptz_xaddr, body)

    def stop(self) -> None:
        body = (
            f"<tptz:Stop><tptz:ProfileToken>{escape(self._ptz_profile())}</tptz:ProfileToken>"
            "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>"
        )
        self.soap.call(self.info.ptz_xaddr, body)

    def get_presets(self) -> list[dict[str, str]]:
        root = self.soap.call(
            self.info.ptz_xaddr,
            f"<tptz:GetPresets><tptz:ProfileToken>{escape(self._ptz_profile())}</tptz:ProfileToken></tptz:GetPresets>",
        )
        return [{"token": p.get("token", ""), "name": text(p, "Name") or p.get("token", "")} for p in find_all(root, "Preset")]

    def goto_preset(self, preset_token: str) -> None:
        self.soap.call(
            self.info.ptz_xaddr,
            f"<tptz:GotoPreset><tptz:ProfileToken>{escape(self._ptz_profile())}</tptz:ProfileToken>"
            f"<tptz:PresetToken>{escape(preset_token)}</tptz:PresetToken></tptz:GotoPreset>",
        )

    def set_preset(self, name: str, preset_token: str | None = None) -> str | None:
        tok = f"<tptz:PresetToken>{escape(preset_token)}</tptz:PresetToken>" if preset_token else ""
        root = self.soap.call(
            self.info.ptz_xaddr,
            f"<tptz:SetPreset><tptz:ProfileToken>{escape(self._ptz_profile())}</tptz:ProfileToken>"
            f"<tptz:PresetName>{escape(name)}</tptz:PresetName>{tok}</tptz:SetPreset>",
        )
        return text(root, "PresetToken")

    # -- Events (PullPoint) ------------------------------------------------
    def create_pullpoint(self, termination: str = "PT10M") -> str:
        if not self.info.events_xaddr:
            raise OnvifError("Camera does not advertise an events service")
        root = self.soap.call(
            self.info.events_xaddr,
            f"<tev:CreatePullPointSubscription><tev:InitialTerminationTime>{termination}</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>",
        )
        ref = find(root, "SubscriptionReference")
        addr = text(ref, "Address")
        if not addr:
            raise OnvifError("PullPoint subscription without address")
        return self._fix_xaddr(addr) or addr

    def pull_messages(self, address: str, timeout_s: int = 10, limit: int = 20) -> list[Notification]:
        root = self.soap.call(
            address,
            f"<tev:PullMessages><tev:Timeout>PT{timeout_s}S</tev:Timeout><tev:MessageLimit>{limit}</tev:MessageLimit></tev:PullMessages>",
            timeout=timeout_s + 10,
        )
        return parse_notifications(root)

    def renew(self, address: str, termination: str = "PT10M") -> None:
        self.soap.call(address, f"<wsnt:Renew><wsnt:TerminationTime>{termination}</wsnt:TerminationTime></wsnt:Renew>")

    def unsubscribe(self, address: str) -> None:
        try:
            self.soap.call(address, "<wsnt:Unsubscribe/>", timeout=3)
        except OnvifError:
            pass


def parse_notifications(root: ET.Element) -> list[Notification]:
    out: list[Notification] = []
    for nm in find_all(root, "NotificationMessage"):
        topic_el = find(nm, "Topic")
        topic = (topic_el.text or "").strip() if topic_el is not None else ""
        # wsnt:Message wraps tt:Message (which carries UtcTime + SimpleItems).
        # Note: Element.iter() yields the element itself first, so skip it.
        msg = find(nm, "Message")
        inner = next((e for e in msg.iter() if e is not msg and local(e.tag) == "Message"), None) if msg is not None else None
        items: dict[str, str] = {}
        utc = None
        if inner is not None:
            ts = inner.get("UtcTime")
            if ts:
                try:
                    utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    utc = None
            for si in find_all(inner, "SimpleItem"):
                if si.get("Name"):
                    items[si.get("Name")] = si.get("Value", "")
        out.append(Notification(topic=topic, items=items, utc_time=utc))
    return out


def classify_notification(n: Notification) -> tuple[str, bool] | None:
    """Map an ONVIF notification to ``(event_type, active)`` or ``None`` if irrelevant."""
    topic = n.topic.lower()
    value = None
    for key in ("IsMotion", "State", "IsPeople", "IsPerson", "IsVehicle", "IsPet", "IsTamper", "Value"):
        if key in n.items:
            value = n.items[key].lower() in ("true", "1")
            break
    if value is None:
        return None
    if any(k in topic for k in ("people", "person", "human")):
        return "person", value
    if any(k in topic for k in ("vehicle", "car")):
        return "vehicle", value
    if any(k in topic for k in ("pet", "animal", "dog", "cat")):
        return "animal", value
    if "motion" in topic or "cellmotion" in topic:
        return "motion", value
    return None


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _clamp(v: float) -> str:
    return f"{max(-1.0, min(1.0, float(v))):.2f}"
