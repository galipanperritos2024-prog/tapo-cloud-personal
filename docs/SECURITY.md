# Seguridad

## Modelo de amenazas (resumen)

| Activo | Amenaza | Medida |
|---|---|---|
| Vídeo de las cámaras | Acceso desde Internet | Nada se publica en Internet: sin UPnP, sin port forwarding; acceso remoto solo por VPN. |
| Vídeo en la LAN | Otro dispositivo de la red abre el stream | MediaMTX exige autenticación en **cada** lector y pregunta al backend: tokens HMAC de 10 min, válidos solo para una cámara; publicar está siempre prohibido. |
| Contraseñas de cámaras | Robo de la BD / logs | Fernet (AES-128-CBC + HMAC-SHA256) en tabla separada; la API nunca devuelve la contraseña ni el usuario completo; los logs pasan por un filtro que enmascara `rtsp://user:***@` y `password=`/`token=`. |
| Tokens de Google | Robo de la BD | Cifrados con Fernet; ámbito mínimo `drive.file` (solo archivos creados por la app); revocables desde la UI. |
| Clave maestra | Robo junto con la BD | Fuera de la BD: variable de entorno (Docker secrets), **Administrador de credenciales de Windows** (DPAPI) con `TAPOCLOUD_USE_KEYRING=1` (lo activa `start-windows.ps1`), o `secret.key` con permisos 0600. |
| Cuenta de la app | Fuerza bruta | scrypt (N=2¹⁵); 10 fallos / 5 min / IP; tiempo constante también para usuarios inexistentes. |
| Sesión | Robo de cookie / CSRF / XSS | Cookie HttpOnly + SameSite=Lax (+Secure con HTTPS); en BD solo el SHA-256 del token; token CSRF ligado a la sesión en cabecera; CSP estricta, `X-Frame-Options: DENY`, `nosniff`; React escapa la salida. |
| Backend | Abuso / SSRF | Validación Pydantic de todas las entradas; IPs de cámara restringidas a rangos privados; rutas de archivo protegidas contra *path traversal*; rate limit global (600/min/IP). |
| Parseo de XML de dispositivos | XML bombs / XXE | `defusedxml`. |
| Procesos internos | Llamadas no autorizadas | camera-service escucha en 127.0.0.1 y exige `X-Internal-Token`; la API de MediaMTX solo en 127.0.0.1 (o red interna de Docker, sin publicar). |
| Grabaciones | Pérdida | Nunca se borra un archivo local sin subida verificada por MD5; MP4 fragmentado resistente a cortes; recuperación de segmentos huérfanos. |

## Buenas prácticas para el usuario

1. Crea en la app Tapo una **cuenta de cámara** dedicada (no reutilices tu contraseña de TP-Link).
2. Mantén las cámaras en una red/VLAN de IoT sin acceso a Internet si tu router lo permite; esta app solo necesita alcanzarlas por LAN.
3. Haz copia de seguridad de la clave maestra (`secret.key` / Administrador de credenciales) junto con la carpeta de datos.
4. Si usas HTTPS (Caddy/Tailscale), pon `TAPOCLOUD_SECURE_COOKIES=true`.
5. Para acceso remoto usa Tailscale o WireGuard ([REMOTE_ACCESS.md](REMOTE_ACCESS.md)); nunca abras puertos.

## Qué no se registra nunca

Contraseñas, tokens OAuth, `client_secret`, credenciales embebidas en URLs. Los mensajes de FFmpeg se redactan antes de escribirse.

## Pendiente

- Cifrado opcional en reposo de las propias grabaciones (hoy se confía en el cifrado del disco: BitLocker/LUKS).
- Multiusuario con roles (hoy: un administrador).
- Auditoría de accesos en la UI.
