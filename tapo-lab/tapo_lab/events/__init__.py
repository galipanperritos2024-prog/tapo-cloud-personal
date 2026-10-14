"""EXPERIMENTAL — event-related configuration via the local API.

Live detections (motion/person/vehicle/pet) are already handled by the
*stable* core through ONVIF PullPoint (``tapocloud.onvif``). This module only
reads the camera-side *configuration* of those detectors, which ONVIF does
not expose, to document per-model capabilities.
"""

from __future__ import annotations

from typing import Any

from tapo_lab.device.adapter import call


def detection_config(camera_id: int, username: str, password: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for feature in ("motion.get", "person.get", "alarm.get"):
        try:
            out[feature] = call(camera_id, feature, username, password)
        except Exception as exc:  # report per feature; one unsupported call must not hide the others
            out[feature] = {"error": str(exc)}
    return out
