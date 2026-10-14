# API REST

Base: `/api`. Documentación interactiva (OpenAPI) en **`/api/docs`** con la aplicación en marcha.

Autenticación por cookie de sesión (`tc_session`, HttpOnly). Toda petición que modifica (POST/PATCH/DELETE) debe enviar la cabecera **`X-CSRF-Token`** con el valor de la cookie `tc_csrf`. Errores: `{"detail": "..."}`.

## Autenticación

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/auth/status` | `{setup_required, authenticated, username}` |
| POST | `/api/auth/setup` | Crea el administrador (solo si no existe ninguno) |
| POST | `/api/auth/login` | `{username, password}` — 10 intentos fallidos / 5 min / IP |
| POST | `/api/auth/logout` | Cierra la sesión |
| POST | `/api/auth/password` | Cambia la contraseña e invalida las demás sesiones |

## Cámaras

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/cameras` | Lista (sin contraseñas; usuario enmascarado) |
| POST | `/api/cameras` | Alta: datos de red, credenciales y configuración de grabación. Rellena modelo/PTZ por ONVIF |
| POST | `/api/cameras/test` | Prueba sin guardar: RTSP principal/secundario (códec, resolución, FPS, audio) + ONVIF |
| GET | `/api/cameras/{id}` | Detalle, estado y `stream_info` |
| PATCH | `/api/cameras/{id}` | Actualización parcial (incl. credenciales, modo, horario) |
| DELETE | `/api/cameras/{id}` | Elimina la cámara (sus grabaciones se conservan) |
| GET | `/api/cameras/{id}/stream?quality=sub\|main` | URLs WHEP/HLS + token de 10 min válido solo para esta cámara |
| GET | `/api/cameras/{id}/preview.jpg` | Miniatura en vivo (substream, caché 10 s) |
| POST | `/api/cameras/{id}/snapshot` | Captura JPEG a resolución completa (también se guarda en disco) |
| POST | `/api/cameras/{id}/record` | `{action: "start"\|"stop", max_minutes?}` grabación manual |
| POST | `/api/cameras/{id}/ptz` | `{action: move\|stop\|goto_preset\|set_preset, pan, tilt, zoom, duration, preset, preset_name}` |
| GET | `/api/cameras/{id}/ptz/presets` | Presets ONVIF |
| POST | `/api/cameras/{id}/onvif/refresh` | Vuelve a leer capacidades ONVIF/PTZ |
| POST | `/api/discovery/scan` | `{subnet_scan: bool}` WS-Discovery + escaneo opcional de la subred privada |

Ejemplo:

```bash
curl -b cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -d '{"name":"Salón","ip":"192.168.1.30","username":"camuser","password":"…",
       "recording_mode":"motion","pre_event_seconds":10,"post_event_seconds":30}' \
  http://localhost:8000/api/cameras
```

Modos: `continuous`, `motion`, `event`, `manual`, `schedule`. Horario: `[{"days":[0,1,2,3,4],"start":"22:00","end":"08:00"}]` (0 = lunes).

## Eventos

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/events?camera_id&type=…&since&until&before_id&unacknowledged&limit` | Lista + total sin revisar |
| GET | `/api/events/{id}/thumbnail` | Miniatura JPEG |
| POST | `/api/events/ack` | `{ids: [..] \| null}` marcar como vistos (null = todos) |

Tipos: `motion, person, vehicle, animal, camera_offline, camera_online, recording_started, recording_finished, storage_error, upload_error`.

## Grabaciones

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/recordings?camera_id&day=AAAA-MM-DD&kind&limit&offset` | Lista (día en hora local) |
| GET | `/api/recordings/timeline?camera_id&day` | Bloques de grabación + eventos de 24 h |
| GET | `/api/recordings/days?camera_id` | Días con grabaciones |
| GET | `/api/recordings/{id}` | Detalle + eventos asociados |
| GET | `/api/recordings/{id}/file[?download=true]` | MP4 con soporte de Range (seek) |
| GET | `/api/recordings/{id}/thumbnail` | Miniatura |
| DELETE | `/api/recordings/{id}` | Borra la copia **local** (la de Drive se mantiene) |

## Google Drive

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/google/status` | Conexión, cuenta, cuota, cola, subida en curso |
| POST | `/api/google/config` | `{client_id, client_secret?, redirect_uri?}` (cliente OAuth propio) |
| POST | `/api/google/connect` | Devuelve `auth_url` de Google (PKCE + state) |
| GET | `/api/google/callback` | Destino OAuth; redirige a `/drive/` |
| POST | `/api/google/disconnect` | Revoca el token y lo borra |
| GET | `/api/google/queue?status` | Cola de subida |
| POST | `/api/google/queue/{id}/retry` · `/api/google/queue/retry-all` | Reintentos |
| POST | `/api/google/queue/enqueue-missing` | Encola grabaciones nunca encoladas |

## Sistema

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/system/status` | CPU, RAM, disco, servicios, streams, grabaciones activas, procesos FFmpeg por cámara, cola |
| GET | `/api/system/logs?file=backend\|camera-service&lines` | Últimas líneas (ya sin secretos) |
| GET/PATCH | `/api/settings` | Ajustes: retención, Drive, límites, red |
| GET | `/api/health` | Comprobación de vida (sin autenticación) |

## Internas (no para navegadores)

- `POST /internal/mediamtx/auth` — MediaMTX valida aquí cada lector (token interno o token de stream). Bloqueada en el nginx de Docker.
- camera-service: `127.0.0.1:8001` con cabecera `X-Internal-Token` (`/status`, `/reload`, `/cameras/{id}/record|snapshot|preview`).
- `tapo-lab` (solo si está activado): `/api/lab/*`, ver [tapo-lab/README.md](../tapo-lab/README.md).
