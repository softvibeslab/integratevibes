# Integratevibes

Telegram Mini App de Notebookvibes para vincular herramientas y redes sociales sin almacenar contraseñas ni tokens OAuth de terceros.

## Módulos

- **Composio:** fuentes de conocimiento, correo, documentos y herramientas de trabajo.
- **Zernio:** conexión de redes sociales mediante el flujo OAuth alojado por Zernio.

Cada proveedor conserva rutas, credenciales y lógica separadas. La interfaz comparte únicamente navegación, estados y componentes visuales.

## Seguridad

- Las rutas Zernio validan `Telegram.WebApp.initData` con HMAC-SHA256 para emitir una sesión backend aleatoria de dos horas; el navegador la conserva solo en memoria y SQLite guarda únicamente su hash.
- El backend aplica la allowlist `TELEGRAM_ALLOWED_USERS` tanto al emitir como al usar la sesión.
- Cada usuario Telegram se mapea a un perfil Zernio independiente.
- La API key de Zernio y el token del bot existen solo en el archivo de entorno del servicio.
- Los callbacks usan un estado aleatorio, ligado al usuario/perfil/plataforma, con 15 minutos de vigencia y un solo uso.
- El callback confirma la conexión consultando las cuentas por API; no confía únicamente en los parámetros del navegador.
- Los webhooks validan `X-Zernio-Signature` con HMAC-SHA256 sobre el cuerpo sin modificar y deduplican por `payload.id`.
- La aplicación no guarda credenciales OAuth de redes sociales ni ejecuta publicaciones.

## Estructura

```text
composio-telegram-miniapp/
  server.py                 Servidor HTTP y rutas de ambos módulos
  index.html                Interfaz móvil
  app.js                    Flujo cliente Telegram/Composio/Zernio
integratevibes/
  telegram_auth.py          Verificación de initData
  integrations_store.py     Mapping y estados de callback en SQLite
  zernio_client.py          Adaptador REST oficial
  integration_service.py    Orquestación y normalización
samples/
  *.service / *.caddy       Referencias de despliegue sin secretos
tests/                      Pruebas unitarias e HTTP
```

## Variables de entorno

Consulta `.env.example`. En producción se usa:

```text
/etc/softvibes-composio-miniapp.env
```

Debe tener permisos `0600`. Nunca copies sus valores al repositorio o a logs.

## Pruebas

```bash
cd /root/integratevibes
python3 -m unittest discover -s tests -v
python3 -m py_compile composio-telegram-miniapp/server.py integratevibes/*.py
node --check composio-telegram-miniapp/app.js
git diff --check
```

## Despliegue actual

- Servicio: `softvibes-composio-miniapp.service`
- Backend: `127.0.0.1:4192`
- URL pública: `https://auth.softvibes.art`
- Estado local: `GET /health`
- Callback Zernio: `/integrations/zernio/callback`
- Webhook Zernio: `/webhooks/zernio`
- Estado persistente: `/var/lib/softvibes-miniapp/integrations.db`

## Rollback

Antes del despliegue Zernio se conservaron:

```text
/opt/softvibes-composio-miniapp.pre-zernio-20260917T044547Z
/etc/softvibes-composio-miniapp.env.pre-zernio-20260917T044547Z
```

Para volver al snapshot, detener el servicio, intercambiar el directorio y env actuales por esos respaldos, iniciar el servicio y verificar `/health` y `/api/status`.

El respaldo Git inmutable previo a Zernio es el tag `pre-zernio-backup`.

## Fuentes oficiales

- https://docs.zernio.com/guides/connecting-accounts
- https://docs.zernio.com/multi-tenant
- https://docs.zernio.com/webhooks
- https://zernio.com/openapi.json
- https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
