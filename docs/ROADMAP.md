# Hoja de ruta

| Fase | Contenido | Estado |
|---|---|---|
| 1 · MVP | Alta manual, RTSP, directo, grabación manual, MP4, almacenamiento local | ✅ |
| 2 · Drive | OAuth, cola, carpetas automáticas, retención, reintentos, MD5 | ✅ |
| 3 · ONVIF | Descubrimiento, perfiles, PTZ, presets, eventos PullPoint | ✅ (validado con tests; pendiente de probar en más modelos reales) |
| 4 · Detección | Movimiento propio, buffer pre-evento, línea de tiempo | ✅ |
| 5 · tapo-lab | Base experimental: guardas, auditoría, adaptador `pytapo`, redacción, metodología | 🧪 base lista |
| 6 · Distribución | Windows nativo + Docker | ✅ · instalador `.msi`/servicio pendiente |

## Verificado en desarrollo

- 46 tests automáticos (cifrado, sesiones, CSRF, rate limit, validación, horarios, comandos FFmpeg, ONVIF, detección, retención, subida reanudable contra un Drive simulado con corte de red, guardas de tapo-lab).
- Prueba de extremo a extremo con cámara simulada (H.264 + G.711) y MediaMTX real: alta, prueba de conexión, segmentos alineados al reloj, grabación manual, capturas, clips de evento con pre-buffer, caída y recuperación de la cámara, parada limpia, autenticación de WebRTC/HLS por token.

## Siguientes pasos propuestos

1. **Probar con tus cámaras reales** (modelos y firmware concretos) y ajustar los *topics* de eventos ONVIF de persona/vehículo/mascota por modelo.
2. **Migraciones con Alembic** antes de cualquier cambio de esquema (hoy `create_all`).
3. **Instalador Windows** (`.msi` con WiX o Inno Setup) que incluya Python embebido, FFmpeg, MediaMTX y la UI precompilada, y registre un servicio (WinSW/NSSM).
4. **IA local opcional** para personas/vehículos/animales/paquetes (YOLO/ONNX Runtime, OpenVINO o Coral) enganchada en `detectors.py`, ejecutada solo sobre fotogramas con movimiento. Sin reconocimiento facial.
5. **Notificaciones** (push vía ntfy/Gotify autoalojado, email, Telegram) con miniatura.
6. **Vista mosaico** multi-cámara y exportación de un intervalo de la línea de tiempo a un único MP4.
7. Multiusuario con roles (admin / solo ver) y registro de accesos.
8. Subida opcional de capturas y miniaturas a Drive; cifrado opcional de vídeos antes de subir.
9. tapo-lab: documentar por modelo (C100/C200/C210/C220/C310/C500…) las funciones disponibles por API local y promover las estables al núcleo tras revisión.
