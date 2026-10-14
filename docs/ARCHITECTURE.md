# Arquitectura

## 1. Visión general

```
                         ┌───────────────────────── Tu red local ─────────────────────────┐
                         │                                                                 │
 ┌────────────┐  RTSP    │  ┌──────────────┐  RTSP (1 conexión     ┌───────────────────┐   │
 │ Cámara     │──────────┼─▶│  MediaMTX    │  por stream)          │  camera-service   │   │
 │ Tapo       │  ONVIF   │  │  (relé)      │◀──────────────────────│  (proceso aparte) │   │
 │ :554 :2020 │◀─────────┼──│              │  rtsp://internal@…    │  · 1 worker/cámara│   │
 └────────────┘  eventos │  └──────┬───────┘                       │  · FFmpeg -c copy │   │
       ▲          PTZ    │         │ WebRTC / LL-HLS               │  · detección      │   │
       │                 │         ▼                               │  · health checks  │   │
       │                 │  ┌──────────────┐   REST + cookies      └────────┬──────────┘   │
       │                 │  │  Navegador   │◀──────────┐                    │ MP4 + filas  │
       │                 │  │  (UI React)  │           │                    ▼              │
       │                 │  └──────────────┘    ┌──────┴───────┐   ┌──────────────────┐   │
       └─────────────────┼──── PTZ / ONVIF ─────│   backend    │◀──│ SQLite/Postgres  │   │
                         │                      │  FastAPI     │   │ + disco local    │   │
                         │                      │  · API REST  │──▶└──────────────────┘   │
                         │                      │  · uploader  │                          │
                         │                      │  · retención │                          │
                         │                      └──────┬───────┘                          │
                         └─────────────────────────────┼──────────────────────────────────┘
                                                       │ HTTPS (OAuth 2.0, Drive API v3)
                                                       ▼
                                               Google Drive del usuario
```

**Ningún puerto se abre hacia Internet.** La única conexión saliente es la del backend hacia Google (OAuth + subidas). El acceso remoto se hace por VPN ([REMOTE_ACCESS.md](REMOTE_ACCESS.md)).

### Procesos

| Proceso | Responsabilidad | Por qué separado |
|---|---|---|
| **MediaMTX** | Mantiene *una* conexión RTSP por stream de cámara y la redistribuye (WebRTC, LL-HLS, RTSP interno). | Las Tapo admiten muy pocas sesiones RTSP simultáneas; sin relé, ver en directo + grabar + detectar las saturaría. |
| **camera-service** (`python -m tapocloud.camera_service`) | Todos los procesos FFmpeg de larga duración, buffers pre-evento, detección, salud de cámaras, grabación manual y capturas. | Un fallo de vídeo nunca tumba la API; puede reiniciarse sin cortar la interfaz. |
| **backend** (`python -m tapocloud.api`) | API REST, autenticación, ONVIF/PTZ, cola de subida a Drive, retención, interfaz web estática. | Es el único proceso con salida a Internet (Google). |

Cada cámara tiene su propio *worker* (hilo) y sus propios procesos FFmpeg hijos, con reinicio y *backoff* exponencial independientes: **una cámara con errores no afecta a las demás**.

## 2. Árbol del proyecto

```
tapo-cloud-personal/
├── backend/
│   ├── pyproject.toml · Dockerfile
│   ├── tapocloud/
│   │   ├── config.py               configuración de despliegue (TAPOCLOUD_*)
│   │   ├── logging_setup.py        logs con redacción automática de secretos
│   │   ├── core/
│   │   │   ├── db.py · models.py   SQLAlchemy 2 (SQLite WAL / PostgreSQL)
│   │   │   ├── crypto.py           Fernet + clave maestra (env / keyring / archivo)
│   │   │   ├── security.py         scrypt, sesiones, CSRF, tokens de stream HMAC
│   │   │   ├── ffmpeg.py           ffprobe + constructores de comandos + proceso supervisado
│   │   │   ├── mediamtx.py         cliente de la API de control de MediaMTX
│   │   │   ├── rtsp.py · naming.py URLs, enmascarado, estructura de carpetas
│   │   │   ├── settings_store.py   ajustes de usuario (con secretos cifrados)
│   │   │   ├── events.py           registro de eventos
│   │   │   └── ratelimit.py · camera_service_client.py
│   │   ├── onvif/                  cliente ONVIF propio (SOAP + WS-Security)
│   │   │   ├── soap.py · device.py  info, perfiles, stream URI, PTZ, PullPoint
│   │   │   └── discovery.py        WS-Discovery + escaneo de subred privada
│   │   ├── camera_service/
│   │   │   ├── __main__.py         API interna (127.0.0.1:8001)
│   │   │   ├── supervisor.py       sincroniza workers con la BD
│   │   │   ├── worker.py           orquestación por cámara
│   │   │   ├── supervised.py       hilo FFmpeg con reinicio/backoff
│   │   │   ├── recorder.py         continua + manual
│   │   │   ├── prebuffer.py        buffer circular y clips de evento
│   │   │   ├── detectors.py        movimiento local + eventos ONVIF
│   │   │   ├── schedule.py         horarios (incl. franjas que cruzan medianoche)
│   │   │   └── catalog.py          registrar grabación + miniatura + cola
│   │   ├── drive/                  oauth.py · client.py · uploader.py
│   │   ├── storage/retention.py
│   │   ├── system/health.py
│   │   └── api/                    main.py · deps.py · schemas.py · routers/*
│   └── tests/                      42 tests (unitarios, API, Drive simulado)
├── frontend/                       Next.js (export estático) + nginx.conf para Docker
│   └── src/{app,components,lib}
├── tapo-lab/                       EXPERIMENTAL (paquete aparte)
├── config/                         mediamtx.yml · Caddyfile
├── scripts/                        install/start Windows · run_all.py · fake_camera.py · dev.sh
├── docker-compose.yml · .env.example
└── docs/
```

## 3. Stack y decisiones técnicas

| Decisión | Motivo |
|---|---|
| **Python + FastAPI** para backend y servicio de cámaras | Mejor ecosistema para ONVIF, análisis de vídeo (numpy/OpenCV/IA futura) y supervisión de procesos; FastAPI da validación (Pydantic) y OpenAPI gratis. |
| **MediaMTX** como relé y servidor WebRTC | Binario único (Windows/Linux/ARM), WebRTC con latencia < 1 s en LAN, LL-HLS de respaldo, API de control para registrar cámaras en tiempo de ejecución (las contraseñas nunca se escriben en disco en su config). |
| **FFmpeg con `-c copy`** | Solo remultiplexa: una Raspberry Pi puede grabar varias cámaras 2K. El audio G.711 de Tapo se convierte a AAC (MP4 no admite G.711); coste despreciable. |
| **MP4 fragmentado** (`frag_keyframe+empty_moov`) | Un corte de luz no corrompe el archivo en curso; lo grabado hasta el último fragmento sigue siendo reproducible y se recupera al arrancar. |
| **Segmentos alineados al reloj** (`-segment_atclocktime`) | Archivos `23-00-00.mp4`, `23-05-00.mp4`… fáciles de encontrar en Drive. |
| **Buffer pre-evento en anillo MPEG-TS de 2 s** | Los fragmentos TS se concatenan sin recodificar; el clip se ensambla en milisegundos. |
| **Detección propia con numpy** (fotogramas 160×90 en gris a 4 fps del substream) | ~1–3 % de un núcleo por cámara, sin dependencia pesada de OpenCV. El punto de enganche para IA (personas/vehículos/animales) está en `detectors.py`. Sin reconocimiento facial. |
| **Cliente ONVIF propio** (≈400 líneas) en vez de `onvif-zeep` | Solo ~10 operaciones necesarias; evita zeep+lxml (problemáticos en ARM/Windows) y permite compensar el desfase de reloj de la cámara (causa nº 1 de fallos de autenticación WS-Security en Tapo). XML parseado con `defusedxml`. |
| **Drive API v3 por REST (httpx)** en vez de `google-api-python-client` | Control total de la subida reanudable (trozos, reanudar tras reinicio, límite de ancho de banda) con mucha menos dependencia. |
| **SQLite (WAL)** por defecto, PostgreSQL opcional | Cero instalación; dos procesos comparten la BD sin bloqueos con WAL + `busy_timeout`. Cambiar a Postgres = una variable de entorno. |
| **Next.js con exportación estática servida por FastAPI** | En producción no hace falta Node: un único puerto y un único origen (cookies/CSRF/OAuth simples), menos superficie de ataque, instalación Windows más sencilla. |
| **Tailwind 4 + SWR + lucide** | UI moderna con muy poco JS (≈120 kB por página) y refresco periódico sencillo. |

## 4. Componentes clave

- **Worker por cámara** (`worker.py`): cada 5 s registra los paths en MediaMTX, comprueba salud (`ready`, bitrate a partir de `bytesReceived`), sondea códec/resolución/FPS con ffprobe, decide qué trabajos deben correr según modo/horario y gestiona los *episodios* de eventos (un evento por actividad, no por fotograma).
- **Degradación elegante**: si MediaMTX no está disponible, los grabadores leen directamente de la cámara y la salud se comprueba por TCP.
- **Diagnóstico útil**: si una cámara no conecta, se sondea directamente para mostrar *por qué* («Credenciales incorrectas (401)…», «Ruta de stream no encontrada…»).

## 5. Modelo de datos

```
users(id, username, password_hash[scrypt], created_at, last_login_at)
sessions(id, token_hash[sha256], csrf_token, user_id→users, expires_at, ip, user_agent)
cameras(id, name, slug, ip, rtsp_port, onvif_port, main_stream_path, sub_stream_path, enabled,
        manufacturer, model, firmware, onvif_enabled, ptz_enabled, ptz_caps{json},
        recording_mode, auto_record, segment_minutes, pre_event_seconds, post_event_seconds,
        motion_source, motion_sensitivity, record_audio, schedule[json],
        status, status_detail, last_seen_at, stream_info{json}, sub_stream_info{json}, created_at, updated_at)
camera_credentials(camera_id→cameras PK, username, password_encrypted[Fernet], updated_at)
recordings(id, camera_id→cameras [SET NULL], kind, status, started_at, ended_at, duration_seconds,
           file_path, size_bytes, thumbnail_path, local_deleted_at, uploaded_at, drive_file_id, created_at)
events(id, camera_id→cameras, type, source, occurred_at, ended_at, thumbnail_path,
       recording_id→recordings [SET NULL], message, details{json}, acknowledged)
upload_queue(id, recording_id→recordings UNIQUE, status, attempts, next_attempt_at, last_error,
             session_uri, bytes_uploaded, drive_path, created_at, updated_at, completed_at)
drive_folders(path PK, folder_id, account)          caché de IDs de carpetas en Drive
settings(key PK, value{json}, encrypted, updated_at) ajustes de usuario y secretos (tokens Google)
```

- Las contraseñas de cámara viven en una tabla aparte que ningún serializador de la API toca.
- Todas las fechas en UTC; la interfaz y las carpetas usan hora local.
- Borrar una cámara **no** borra sus grabaciones (`SET NULL`): siguen listadas, subiéndose y sujetas a retención.

## 6. Flujo RTSP → grabación → Drive

```
1. camera-service registra cam{N}_main (siempre activo) y cam{N}_sub (bajo demanda) en MediaMTX.
2. FFmpeg lee rtsp://internal:<token>@mediamtx/cam{N}_main  (MediaMTX valida el token con el backend)
3. Continuo:  -c:v copy -c:a aac -f segment -segment_time 300 -segment_atclocktime 1 → .incoming/cam{N}/2026-10-14_23-05-00.mp4
   Evento:    anillo de fragmentos TS de 2 s; al detectar, copia [t-pre, t+post] → concat -c copy → 23-04-52_event.mp4
4. Al cerrarse cada segmento FFmpeg añade una línea al CSV → rename atómico a  Salon/2026/10/14/23-05-00.mp4
5. catalog: fila en recordings + miniatura + fila en upload_queue (pending)
6. uploader (backend): carpeta TapoCloud/Salon/2026/10/14 (caché) → subida reanudable en trozos de 8 MiB
   → Google devuelve md5Checksum → se compara con el MD5 local → recordings.uploaded_at
   · sin Internet: estado retry con backoff 30 s → 1 h (+jitter); la grabación sigue en local
   · reinicio a mitad: la session URI persistida permite continuar en el último byte confirmado
   · idempotencia: antes de subir se busca en Drive un archivo con appProperties.tapocloud_recording=<id>
7. retención: solo borra archivos con uploaded_at (verificado) y según la política elegida
```

## 7. Seguridad

Ver [SECURITY.md](SECURITY.md).

## 8. Interfaz

Tema oscuro, tarjetas grandes, responsive (barra lateral en escritorio, barra inferior en móvil). Colores de estado: **verde** conectado, **amarillo** advertencia, **rojo** desconectado.

| Pantalla | Contenido |
|---|---|
| Inicio | Cámaras online/offline, almacenamiento, Drive, cola, miniaturas, eventos y grabaciones recientes, estado de servicios |
| Cámaras | Tarjetas con miniatura + tabla, asistente «Añadir cámara» (búsqueda → credenciales con prueba → modo) |
| Cámara | Directo WebRTC (fluida/alta calidad), captura, grabación manual, PTZ, información técnica, configuración de grabación con horarios, credenciales |
| Eventos | Filtros por cámara/tipo, miniaturas, reproducción del clip asociado |
| Grabaciones | Filtros, **línea de tiempo de 24 h**, reproductor con *seek*, descarga, enlace a Drive |
| Google Drive | Conexión OAuth, cuota, progreso de subida, opciones, cola con reintentos |
| Configuración | Retención, límites, red, acceso remoto, contraseña |
| Sistema | CPU/RAM/disco, servicios, procesos FFmpeg por cámara, cola, visor de logs |

## 9. Docker

`docker-compose.yml`: `frontend` (nginx: UI + proxy de `/api`, `/webrtc`, `/hls`), `backend`, `camera-service` (misma imagen), `streaming` (MediaMTX), `database` (PostgreSQL, perfil opcional), `reverse-proxy` (Caddy HTTPS, perfil opcional). Solo se publican el puerto de la UI (8080) y el UDP 8189 de WebRTC.

## 10. MVP (fase 1) y siguientes

Las fases 1–4 están implementadas; la 5 (tapo-lab) tiene su base experimental y la 6 tiene Windows nativo y Docker. Detalle y siguientes pasos en [ROADMAP.md](ROADMAP.md).

## Escalabilidad

- 1–4 cámaras: cualquier PC; Raspberry Pi 4/5 sin problema (sin recodificación).
- Hasta 16: cada cámara ≈ 1 FFmpeg de grabación (+1 de detección si se usa la local) con CPU muy baja; el límite real suele ser el disco y la subida a Internet (4 cámaras 2K a ~2 Mbps ≈ 85 GB/día).
