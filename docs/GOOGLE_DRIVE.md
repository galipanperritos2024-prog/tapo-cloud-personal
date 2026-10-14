# Configurar Google Drive

La app sube los vídeos a **tu** Google Drive usando tu **propio** cliente OAuth. No hay servidores intermedios ni introduces tu contraseña de Google en la app.

## 1. Crear el cliente OAuth (una vez, ~5 minutos)

1. Entra en [Google Cloud Console](https://console.cloud.google.com/) y crea un proyecto (p. ej. «Tapo Cloud Personal»).
2. **APIs y servicios → Biblioteca** → busca **Google Drive API** → *Habilitar*.
3. **Pantalla de consentimiento OAuth**: tipo *Externo*; nombre de la app; tu email. En *Usuarios de prueba* añade tu cuenta de Gmail.
   - El ámbito que usa la app es `.../auth/drive.file` (no sensible): no requiere verificación de Google.
   - Opcional: *Publicar la app* para que el token no caduque a los 7 días (en modo prueba Google caduca los refresh tokens a la semana; la app avisará y bastará con reconectar).
4. **Credenciales → Crear credenciales → ID de cliente de OAuth** → tipo **Aplicación web**.
   - *URI de redireccionamiento autorizados*:
     - Windows/nativo: `http://localhost:8000/api/google/callback`
     - Docker: `http://localhost:8080/api/google/callback`
5. Copia el **Client ID** y el **Client secret**.

## 2. Conectar

En la app: **Google Drive → Guardar credenciales OAuth → Conectar Google Drive**. Google te pedirá permiso; al aceptar vuelves a la app con «Conectado».

> Google solo permite `localhost` (o HTTPS con dominio) como redirección. Haz este paso **desde un navegador en el mismo equipo** donde corre la app (o con un túnel `ssh -L`). Después ya puedes usar la app desde cualquier dispositivo.

## 3. Qué se sube y cómo

```
TapoCloud/
└── Salon/
    └── 2026/
        └── 10/
            └── 14/
                ├── 23-00-00.mp4
                ├── 23-05-00.mp4
                └── 23-04-52_event.mp4
```

- Tipos a subir configurables (continuas, eventos, manuales) y límite de ancho de banda.
- Subida **reanudable** en trozos de 8 MiB; si se corta Internet o se reinicia el equipo, continúa en el último byte confirmado.
- Reintentos con espera exponencial (30 s, 1 min, 2 min… hasta 1 h).
- Tras subir, se compara el **MD5** que calcula Google con el del archivo local; solo entonces la grabación cuenta como subida y puede borrarse en local según la política de retención.

## Revocar el acceso

Desde la app (**Desconectar**) o en [myaccount.google.com/permissions](https://myaccount.google.com/permissions). Los archivos ya subidos permanecen en tu Drive.
