# Arquitectura Zernio

## Flujo por usuario

```text
Telegram WebApp initData
  -> verificación HMAC + antigüedad + allowlist
  -> telegram_user_id
  -> SQLite zernio_profiles
  -> profileId Zernio
```

El `profileId` nunca se devuelve al frontend. En la primera consulta se crea de forma idempotente; en las siguientes se reutiliza el mapping local.

## Flujo OAuth estándar

```text
POST /api/zernio/connect {platform}
  -> estado aleatorio de 15 minutos
  -> GET Zernio /v1/connect/{platform}
  -> authUrl HTTPS
  -> consentimiento del usuario
  -> GET /integrations/zernio/callback?state=...&connected=...&profileId=...
  -> consumo único del estado
  -> comprobación profileId/plataforma
  -> GET /v1/accounts?profileId=...
  -> pantalla de éxito o recuperación
```

Se usa `headless=false`. El selector alojado por Zernio maneja páginas, organizaciones y recursos específicos del proveedor.

WhatsApp añade `onboarding=api` y `signup=hosted`. Telegram usa el flujo separado de código temporal documentado por Zernio.

## Webhooks

La suscripción `integratevibes-account-events` recibe:

- `account.connected`
- `account.disconnected`

El endpoint comprueba la firma hexadecimal `X-Zernio-Signature`, calculada como HMAC-SHA256 del cuerpo crudo. Los eventos repetidos responden `200` con `accepted=false`.

Los webhooks son una señal; la UI sigue reconciliando el estado contra `GET /v1/accounts`.

## Persistencia

SQLite contiene únicamente:

- mapping inmutable Telegram user ID -> Zernio profile ID;
- estados efímeros de callback de un solo uso, con purga de vencidos;
- hashes SHA-256 de códigos Telegram ligados a usuario/perfil, con máximo de 15 minutos;
- IDs de webhooks procesados, con retención de 90 días.

No contiene API keys, tokens OAuth, contraseñas, códigos Telegram en claro ni códigos 2FA.

## Límites deliberados

- Una cuenta por plataforma en cada perfil Zernio.
- Reconectar puede reemplazar la cuenta anterior.
- TikTok muestra una advertencia destructiva antes de reconectar.
- No se publican posts ni se ejecutan acciones sociales desde esta Mini App.
- OAuth, consentimiento y 2FA los completa personalmente el usuario.
