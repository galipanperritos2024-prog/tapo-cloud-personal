# Instalación

Requisitos comunes: las cámaras y el equipo en la misma red local; en cada Tapo, una **cuenta de cámara** (app Tapo → Configuración avanzada → Cuenta de la cámara).

## Windows 10/11 (nativo, recomendado para empezar)

1. Instala Python 3.11+ y Node.js LTS (este último solo para compilar la interfaz una vez):
   ```powershell
   winget install Python.Python.3.12
   winget install OpenJS.NodeJS.LTS
   ```
2. Descarga el proyecto y ejecuta el instalador (sin permisos de administrador):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
   ```
   Crea `.venv`, instala el backend, descarga **FFmpeg** y **MediaMTX** en `bin\` y compila la interfaz.
3. Arranca:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1
   ```
   Se abre `http://localhost:8000`. Para usarlo desde el móvil en casa: `http://<IP-del-PC>:8000`.
4. Firewall: la primera vez Windows preguntará por `python.exe` y `mediamtx.exe`; permite **solo redes privadas**. O, como administrador: `install-windows.ps1 -ConfigureFirewall` (crea reglas para 8000, 8888/8889 TCP y 8189 UDP, perfil privado).

Datos en `%LOCALAPPDATA%\TapoCloud\` (BD, grabaciones, miniaturas, logs). Para grabar en otro disco: variable `TAPOCLOUD_RECORDINGS_DIR=D:\TapoCloud`.

Arranque automático: crea una tarea en el Programador de tareas («Al iniciar sesión») que ejecute `powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <ruta>\scripts\start-windows.ps1`. (Un instalador `.msi` con servicio de Windows está en la [hoja de ruta](ROADMAP.md).)

## Docker (Linux, NAS, Raspberry Pi 4/5)

```bash
cp .env.example .env
# Edita: HOST_LAN_IP (IP del equipo), TAPOCLOUD_SCAN_NETWORKS (tu LAN), RECORDINGS_PATH
# Genera secretos:
python3 -c "import secrets; print('TAPOCLOUD_INTERNAL_TOKEN=' + secrets.token_urlsafe(32))" >> .env
docker compose up -d --build
```

UI en `http://<HOST_LAN_IP>:8080`. Puertos publicados: 8080/TCP (UI + señalización) y 8189/UDP (vídeo WebRTC).

- **PostgreSQL**: añade `TAPOCLOUD_DATABASE_URL=postgresql+psycopg://tapocloud:<pass>@database:5432/tapocloud` y `POSTGRES_PASSWORD=<pass>` al `.env`, luego `docker compose --profile postgres up -d`.
- **HTTPS en la LAN**: `docker compose --profile proxy up -d` (Caddy con CA interna) y `TAPOCLOUD_SECURE_COOKIES=true`.
- **Búsqueda de cámaras**: el multicast ONVIF no atraviesa la red bridge de Docker; usa la opción «escanear subred» (usa `TAPOCLOUD_SCAN_NETWORKS`) o introduce la IP manualmente.
- **Google OAuth**: registra `http://localhost:8080/api/google/callback` y pon ese valor en `TAPOCLOUD_GOOGLE_REDIRECT_URI`; conecta Drive desde un navegador en el propio equipo (o con un túnel SSH `ssh -L 8080:localhost:8080 nas`).

Copia de seguridad: volumen `tapo-data` (BD, `secret.key`, `internal.token`) + carpeta `RECORDINGS_PATH`.

## Linux nativo / Raspberry Pi OS (systemd)

```bash
sudo apt install python3-venv ffmpeg
git clone <repo> /opt/tapocloud && cd /opt/tapocloud
python3 -m venv .venv && .venv/bin/pip install -e backend
# MediaMTX (elige amd64 / arm64v8 / armv7):
curl -L https://github.com/bluenviron/mediamtx/releases/download/v1.15.3/mediamtx_v1.15.3_linux_arm64v8.tar.gz | tar xz -C bin mediamtx
(cd frontend && npm ci && npm run build)   # o copia frontend/out desde otro equipo
```

`/etc/systemd/system/tapocloud.service`:

```ini
[Unit]
Description=Tapo Cloud Personal
After=network-online.target
Wants=network-online.target

[Service]
User=tapocloud
WorkingDirectory=/opt/tapocloud
Environment=TAPOCLOUD_DATA_DIR=/var/lib/tapocloud
ExecStart=/opt/tapocloud/.venv/bin/python scripts/run_all.py
Restart=always
KillSignal=SIGINT
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin tapocloud && sudo mkdir -p /var/lib/tapocloud && sudo chown tapocloud /var/lib/tapocloud
sudo systemctl enable --now tapocloud
```

## Sin cámara (pruebas)

`python scripts/fake_camera.py` crea una cámara RTSP simulada (H.264 + audio G.711, como una Tapo) en `127.0.0.1:8654`, usuario `camuser`, contraseña `campass`. Arranca la app con `TAPOCLOUD_ALLOW_PUBLIC_CAMERA_IPS=true` (127.0.0.1 no es una IP de LAN) y añádela con puerto RTSP 8654 y ONVIF desactivado.

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| «Credenciales incorrectas (401)» | Estás usando la cuenta TP-Link; usa la *cuenta de cámara*. |
| ONVIF falla pero RTSP funciona | Algunos modelos (p. ej. C100 antiguos) no tienen ONVIF o requieren firmware reciente: la app desactiva PTZ/eventos ONVIF y usa detección propia. |
| Directo en negro o «códec no soportado» | El navegador no reproduce el códec (H.265 en algunos navegadores). Usa Chrome/Edge/Safari actualizados o pon la cámara en H.264. |
| WebRTC no conecta y usa HLS | UDP 8189 bloqueado (firewall) o, en Docker, `HOST_LAN_IP` sin configurar. |
| Cámara alterna online/offline | Wi-Fi débil: los segmentos se reanudan solos; considera bajar la calidad o mejorar la cobertura. |
