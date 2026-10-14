"""EXPERIMENTAL — native PTZ operations that ONVIF does not cover (motor calibration,
absolute motor steps). Standard pan/tilt/presets stay in the stable core (ONVIF)."""

from __future__ import annotations

from tapo_lab.device.adapter import call


def move(camera_id: int, username: str, password: str, x: int, y: int):
    """Relative motor move in device units (typically -360..360 / -45..45)."""
    x, y = max(-360, min(360, int(x))), max(-90, min(90, int(y)))
    return call(camera_id, "ptz.move", username, password, [x, y])


def calibrate(camera_id: int, username: str, password: str):
    return call(camera_id, "ptz.calibrate", username, password)
