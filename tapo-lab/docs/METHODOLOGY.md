# Metodología de investigación (EXPERIMENTAL)

Objetivo: **interoperabilidad** con dispositivos propios, documentada y reproducible.

## Técnicas permitidas

| Técnica | Ejemplo |
|---|---|
| Interfaces documentadas o estándar | RTSP, ONVIF (en el núcleo), WS-Discovery |
| Bibliotecas *open source* existentes | `pytapo`, `python-kasa` y su documentación pública |
| Observar **tu propio** tráfico de LAN | Wireshark/tcpdump en tu red: qué puertos/protocolos usa tu cámara al hablar con tu app en casa |
| Llamar a la API local **con tus credenciales** | Mediante `tapo_lab.device.adapter` (whitelist + auditoría) |
| Comparar comportamientos | Cambiar un ajuste en la app Tapo y comprobar qué refleja la API local |

## Prohibido

- Eludir, debilitar o forzar cualquier autenticación (incluida la de tus propios dispositivos por fuerza bruta).
- Interceptar o modificar tráfico hacia/desde servidores de TP-Link, o sondearlos.
- Extraer claves, certificados o credenciales del firmware o de la app para suplantarla.
- Acceder a dispositivos, cuentas o redes de terceros.
- Eludir Tapo Care, pagos, DRM o límites de servicio.
- Publicar datos personales: usa siempre `tapo_lab.protocol.redact` antes de compartir notas.

## Proceso

1. **Hipótesis**: «el modelo X expone el estado del LED en la API local».
2. **Prueba** con `POST /api/lab/cameras/{id}/call` (`as_note: true`).
3. **Registro** en `research/<modelo>.md` usando la plantilla: firmware, función, resultado redactado, efecto observado.
4. **Repetición** en otro firmware/modelo si es posible.
5. **Propuesta** de promoción al núcleo (ver criterios en el README).

## Riesgos y precauciones

- Las operaciones de escritura (`writes: true`) cambian la configuración de la cámara: pruébalas cuando puedas comprobar el resultado físicamente.
- `ptz.calibrate` mueve los motores por todo su recorrido.
- Las APIs locales no documentadas pueden cambiar con cualquier actualización de firmware; por eso esto no forma parte del núcleo estable.
