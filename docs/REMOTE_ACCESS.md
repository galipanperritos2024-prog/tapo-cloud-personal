# Acceso remoto seguro

**Regla:** la app nunca abre puertos en el router ni usa UPnP, y no debes abrirlos tú. Para ver las cámaras fuera de casa, crea una **VPN**: tu móvil «entra» en tu red y la app funciona igual que en casa.

## Opción A — Tailscale (lo más sencillo)

1. Instala Tailscale en el equipo que ejecuta la app y en tu móvil/portátil; inicia sesión con la misma cuenta.
2. Desde fuera de casa abre `http://<IP-100.x-del-equipo>:8000` (nativo) o `:8080` (Docker).
3. WebRTC funciona a través de Tailscale (es tráfico directo cifrado con WireGuard); si alguna red lo bloquea, el reproductor pasa a HLS automáticamente.
4. HTTPS opcional con certificado válido: `tailscale serve --bg 8000` (o `tailscale cert`) y `TAPOCLOUD_SECURE_COOKIES=true`.

No uses **Tailscale Funnel**: publicaría la app en Internet.

## Opción B — WireGuard en el router o en el equipo

1. Si tu router lo soporta (muchos lo hacen), activa su servidor WireGuard; si no, instala WireGuard en el equipo (el único puerto expuesto es el UDP de WireGuard, autenticado por clave).
2. Importa el perfil en el móvil y accede con la IP local del equipo.

## Por qué no abrir puertos

Una cámara o un NVR expuestos son objetivo constante de escaneos automáticos. Con una VPN, lo único accesible desde Internet es un servicio criptográficamente autenticado y diseñado para ello.
