# Tapo Cloud Personal

Grabación y copia en **tu propio Google Drive** para **tus** cámaras Tapo (y cualquier cámara RTSP/ONVIF), sin suscripción y sin exponer las cámaras a Internet.

```
Cámara Tapo ──RTSP/ONVIF──▶ MediaMTX (relé) ──▶ camera-service (FFmpeg -c copy) ──▶ MP4 locales ──▶ cola ──▶ Google Drive
                                   │
                                   └──WebRTC (<1 s) / LL-HLS──▶ navegador
```

## Qué hace (v0.1)

| Función | Estado |
|---|---|
| Alta de cámaras (manual o **búsqueda ONVIF/escaneo LAN**) con prueba de conexión | ✅ |
| Vídeo en directo en el navegador (**WebRTC**, respaldo LL-HLS), calidad fluida / alta | ✅ |
| Grabación **continua 24/7**, **por horario**, **por movimiento**, **por evento** y **manual** | ✅ |
| Segmentos de 1/5/10/15/30/60 min alineados al reloj, **sin recodificar** (`-c copy`), MP4 fragmentado a prueba de cortes de luz | ✅ |
| **Buffer pre-evento** (0–30 s antes, 10–120 s después) | ✅ |
| Detección: eventos ONVIF de la cámara (movimiento/persona/vehículo/animal) + **detección propia** por análisis de imagen | ✅ |
| Capturas, miniaturas, **PTZ** (flechas, zoom si existe, presets) | ✅ |
| **Google Drive** por OAuth 2.0 (`drive.file`), carpetas `TapoCloud/Cámara/AAAA/MM/DD/`, subida reanudable, reintentos exponenciales, **verificación MD5** | ✅ |
| Política de retención local (hasta subir / 24 h / 3 d / 7 d / 30 d) — **nunca se borra nada sin subida verificada** | ✅ |
| Eventos, grabaciones con **línea de tiempo**, estado del sistema y logs | ✅ |
| Seguridad: credenciales cifradas, sesiones, CSRF, rate limiting, tokens de stream por cámara | ✅ |
| Windows (nativo), Linux, Docker, Raspberry Pi/NAS | ✅ (ver [instalación](docs/INSTALL.md)) |
| `tapo-lab`: interoperabilidad **EXPERIMENTAL** (privacidad, LED, visión nocturna, SD…) | 🧪 opcional |

## Arranque rápido

### Windows 11

```powershell
git clone <este-repo> TapoCloud; cd TapoCloud
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1
```

Se abre `http://localhost:8000`: creas tu usuario → **Añadir cámara** → buscar → credenciales → modo de grabación → listo.

### Docker (Linux, NAS, Raspberry Pi)

```bash
cp .env.example .env    # pon HOST_LAN_IP y genera los secretos
docker compose up -d --build
# http://<IP-del-equipo>:8080
```

### Probar sin cámara

```bash
python scripts/fake_camera.py      # cámara simulada: rtsp://camuser:campass@127.0.0.1:8654/stream1
TAPOCLOUD_ALLOW_PUBLIC_CAMERA_IPS=true python scripts/run_all.py --open
```

## Antes de añadir una Tapo

En la app Tapo: **Cámara → Ajustes → Configuración avanzada → Cuenta de la cámara**: crea usuario y contraseña. Esas credenciales (no las de tu cuenta TP-Link) sirven para RTSP (puerto 554) y ONVIF (puerto 2020).

## Documentación

- [Arquitectura, stack y decisiones](docs/ARCHITECTURE.md) · [Modelo de datos y flujo de grabación](docs/ARCHITECTURE.md#5-modelo-de-datos)
- [API REST](docs/API.md)
- [Seguridad](docs/SECURITY.md)
- [Instalación (Windows, Docker, Raspberry Pi, servicio Linux)](docs/INSTALL.md)
- [Configurar Google Drive](docs/GOOGLE_DRIVE.md)
- [Acceso remoto seguro (Tailscale / WireGuard)](docs/REMOTE_ACCESS.md)
- [Hoja de ruta](docs/ROADMAP.md)
- [tapo-lab (EXPERIMENTAL)](tapo-lab/README.md)

## Estructura

```
backend/      Python 3.11+ · FastAPI · SQLAlchemy · FFmpeg   (API, camera-service, uploader)
frontend/     Next.js 15 · React 19 · TypeScript · Tailwind 4 (exportación estática)
tapo-lab/     módulo EXPERIMENTAL, separado y desactivado por defecto
config/       mediamtx.yml, Caddyfile
scripts/      instalación Windows, lanzador, cámara simulada
docs/         documentación
```

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]" -e tapo-lab
.venv/bin/pytest backend/tests tapo-lab/tests     # 46 tests
./scripts/dev.sh --fake                             # UI con recarga en caliente en :3000
```

Uso previsto: **tus** cámaras, **tu** red y **tu** cuenta. Respeta la normativa de videovigilancia y privacidad de tu país (p. ej. no grabar la vía pública ni a terceros sin base legal).
