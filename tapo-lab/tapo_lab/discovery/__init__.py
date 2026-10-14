"""EXPERIMENTAL — which interfaces does each of *your* cameras expose on the LAN?

Read-only TCP reachability checks against cameras already registered in Tapo
Cloud Personal. No packets are sent to any other host and nothing is
authenticated here.
"""

from __future__ import annotations

import socket

from tapo_lab.guard import experimental, resolve_own_camera

PORTS = {
    554: "RTSP (vídeo)",
    2020: "ONVIF (Tapo)",
    443: "API local HTTPS (app Tapo)",
    80: "HTTP",
    8800: "Streaming propietario (algunos modelos)",
}


@experimental("discovery.interfaces")
def interfaces(camera_id: int, timeout: float = 1.0) -> dict[str, object]:
    host, name = resolve_own_camera(camera_id)
    result = {}
    for port, label in PORTS.items():
        try:
            with socket.create_connection((host, port), timeout=timeout):
                result[str(port)] = {"open": True, "service": label}
        except OSError:
            result[str(port)] = {"open": False, "service": label}
    return {"camera": name, "host": host, "ports": result}
