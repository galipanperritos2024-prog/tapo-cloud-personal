"""EXPERIMENTAL — adapter over the camera's local API via ``pytapo``.

Design
------
* **Whitelist**: only the operations listed in :data:`FEATURES` can be
  invoked. Each maps to candidate ``pytapo`` method names; the first one that
  exists in the installed version is used, so library renames degrade to a
  clear "not supported" instead of a crash.
* **Credentials are per call and never stored** by the lab. The camera's
  local API usually needs the *owner* credentials (user ``admin`` + the
  TP-Link account password) for configuration endpoints; the RTSP "camera
  account" only works for some read operations. That is the camera's own
  authentication — we use it, we never bypass it.
* Results are returned raw and also suitable to paste (redacted) into
  ``tapo-lab/research/`` notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tapo_lab.guard import experimental, resolve_own_camera


@dataclass(frozen=True)
class Feature:
    key: str
    description: str
    methods: tuple[str, ...]
    writes: bool = False


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("info", "Información básica del dispositivo", ("getBasicInfo",)),
        Feature("privacy.get", "Estado del modo privacidad", ("getPrivacyMode",)),
        Feature("privacy.set", "Activar/desactivar modo privacidad", ("setPrivacyMode",), writes=True),
        Feature("led.get", "Estado del LED", ("getLED",)),
        Feature("led.set", "Encender/apagar LED de estado", ("setLEDEnabled",), writes=True),
        Feature("nightvision.get", "Modo de visión nocturna", ("getDayNightMode", "getNightVisionModeConfig")),
        Feature("nightvision.set", "Visión nocturna: on/off/auto", ("setDayNightMode",), writes=True),
        Feature("motion.get", "Configuración de detección de movimiento", ("getMotionDetection",)),
        Feature("person.get", "Configuración de detección de personas", ("getPersonDetection",)),
        Feature("alarm.get", "Configuración de alarma (luz/sonido)", ("getAlarm",)),
        Feature("sdcard.get", "Estado de la tarjeta SD", ("getSdCard", "getSDCard")),
        Feature("presets.get", "Presets PTZ guardados", ("getPresets",)),
        Feature("ptz.move", "Mover motor (x, y) — PTZ nativo", ("moveMotor",), writes=True),
        Feature("ptz.calibrate", "Calibrar motores PTZ", ("calibrateMotor",), writes=True),
        Feature("time.get", "Hora del dispositivo", ("getTime",)),
    )
}


class LabError(RuntimeError):
    pass


def _client(host: str, username: str, password: str):
    try:
        from pytapo import Tapo  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LabError("Instala el extra opcional: pip install -e tapo-lab[pytapo]") from exc
    try:
        return Tapo(host, username, password)
    except Exception as exc:  # pytapo raises plain Exceptions on auth/connection errors
        raise LabError(f"No se pudo autenticar con la API local de la cámara: {exc}") from exc


def list_features() -> list[dict[str, Any]]:
    return [{"key": f.key, "description": f.description, "writes": f.writes, "experimental": True} for f in FEATURES.values()]


@experimental("device.call")
def call(camera_id: int, feature: str, username: str, password: str, args: list[Any] | None = None) -> Any:
    """Run one whitelisted feature against one of *your* cameras."""
    spec = FEATURES.get(feature)
    if spec is None:
        raise LabError(f"Función no permitida: {feature}")
    host, _name = resolve_own_camera(camera_id)
    client = _client(host, username, password)
    method = next((getattr(client, m) for m in spec.methods if hasattr(client, m)), None)
    if method is None:
        raise LabError(f"La versión instalada de pytapo no soporta '{feature}'")
    try:
        return method(*(args or []))
    except Exception as exc:
        raise LabError(f"La cámara rechazó '{feature}': {exc}") from exc
