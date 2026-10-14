# tapo-lab — EXPERIMENTAL

> ⚠️ **EXPERIMENTAL.** Interoperabilidad con funciones de Tapo que **no** ofrecen RTSP ni ONVIF. Separado del núcleo estable, desactivado por defecto y desactivable por completo.

## Alcance

**Sí:** documentar y usar, sobre **tus** cámaras, **tu** cuenta y **tu** red:
descubrimiento de interfaces locales, estados y configuración, PTZ nativo (calibración), configuración de eventos, modo privacidad, LED, visión nocturna, tarjeta SD y otras funciones que la app Tapo expone en la API local de la cámara.

**No (por diseño):** saltarse autenticación; atacar o sondear servidores de TP-Link; acceder a cámaras o cuentas ajenas; extraer credenciales de terceros; eludir pagos, Tapo Care o DRM. Ver [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Activación

```bash
pip install -e "tapo-lab[pytapo]"          # en el mismo entorno que el backend
TAPOCLOUD_TAPO_LAB_ENABLED=true            # en .env o variables de entorno
```

Con el *flag* desactivado (por defecto) el backend no monta ninguna ruta del laboratorio y cualquier función lanza `LabDisabled`. Para eliminarlo del todo basta con no instalar el paquete: **el núcleo nunca lo importa** salvo con el *flag* activo.

## Salvaguardas (`tapo_lab/guard.py`)

- Solo cámaras **ya registradas** en Tapo Cloud Personal y con **IP privada**.
- **Lista blanca** de operaciones (`device/adapter.py → FEATURES`); nada fuera de ella es invocable.
- Credenciales **por llamada**, nunca almacenadas por el laboratorio. Se usa la autenticación propia de la cámara (normalmente usuario `admin` + contraseña de tu cuenta TP-Link para la API local de configuración).
- **Auditoría** de cada intento, incluidos los rechazados: `<data_dir>/logs/tapo-lab-audit.log` (sin secretos).
- Respuestas marcadas con `X-Experimental: tapo-lab` y **redactadas** (MAC, números de serie, tokens, IPs públicas).

## Estructura

```
tapo_lab/
├── guard.py          activación, propiedad del dispositivo, auditoría
├── api.py            /api/lab/* (montado solo si está activado)
├── discovery/        interfaces locales expuestas por cada cámara (solo TCP connect)
├── device/adapter.py funciones whitelisted sobre la API local vía pytapo
├── events/           configuración de detectores (ONVIF ya cubre los eventos en vivo)
├── ptz/              movimiento nativo y calibración
└── protocol/         redacción y generación de notas de investigación
research/             notas por modelo (plantilla en research/TEMPLATE.md)
docs/METHODOLOGY.md   técnicas permitidas y prohibidas
```

## Uso (API)

```bash
GET  /api/lab/features                       # funciones disponibles (todas EXPERIMENTAL)
GET  /api/lab/cameras/1/interfaces           # puertos 554/2020/443/80/8800
POST /api/lab/cameras/1/call  {"feature": "privacy.get", "username": "admin", "password": "…", "as_note": true}
POST /api/lab/cameras/1/call  {"feature": "led.set", "username": "admin", "password": "…", "args": [false]}
```

Con `as_note: true` la respuesta incluye un bloque Markdown redactado listo para pegar en `research/<modelo>.md`.

## Promoción al núcleo

Una función pasa al núcleo estable solo si: funciona en varios modelos/firmwares documentados en `research/`, tiene tests, degrada con elegancia cuando no está soportada, y no requiere credenciales de la cuenta TP-Link (o las guarda cifradas con consentimiento explícito).
