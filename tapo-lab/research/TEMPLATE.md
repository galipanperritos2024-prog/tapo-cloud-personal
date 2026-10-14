# <Modelo> — notas de interoperabilidad (EXPERIMENTAL)

| Campo | Valor |
|---|---|
| Modelo / hardware | p. ej. C200 v4 |
| Firmware | p. ej. 1.4.x Build … |
| ONVIF | sí / no — puerto 2020 |
| RTSP | stream1 (resolución, códec), stream2 |
| Interfaces locales | resultado de `GET /api/lab/cameras/{id}/interfaces` |
| Fecha | AAAA-MM-DD |

## Funciones probadas

| Función (`feature`) | Lectura | Escritura | Credenciales necesarias | Notas |
|---|---|---|---|---|
| privacy.get / privacy.set | ✅/❌ | ✅/❌ | admin + cuenta TP-Link / cuenta de cámara | |
| led.get / led.set | | | | |
| nightvision.get / set | | | | |
| sdcard.get | | | | |
| motion.get / person.get | | | | |
| ptz.move / ptz.calibrate | | | | |

## Eventos ONVIF observados

| Topic | SimpleItem | Significado |
|---|---|---|
| tns1:RuleEngine/CellMotionDetector/Motion | IsMotion=true/false | movimiento |

## Respuestas (redactadas)

<!-- Pega aquí los bloques Markdown devueltos con "as_note": true -->
