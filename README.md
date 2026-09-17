# Integratevibes

Telegram Mini App de Softvibes para conectar fuentes mediante Composio y, en la siguiente fase, cuentas sociales mediante Zernio.

## Respaldo inicial

El primer commit y el tag `pre-zernio-backup` preservan la versión productiva de la Mini App antes de integrar Zernio. Los archivos `server.py`, `app.js` e `index.html` fueron comparados con `/opt/softvibes-composio-miniapp` por SHA-256 antes del respaldo.

## Verificación

```bash
python3 -m unittest discover -s tests -v
```

## Seguridad

No se versionan API keys, tokens OAuth, tokens de Telegram, archivos `.env`, bases de estado ni URLs de capacidad activas. Los secretos deben permanecer en el environment file del servicio o en un gestor de secretos.
