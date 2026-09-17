#!/usr/bin/env python3
"""Authenticated backend for a Telegram Mini App that links curated Composio apps."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from integratevibes.integration_service import (
    IntegrationService,
    IntegrationServiceError,
    verify_zernio_webhook,
)
from integratevibes.integrations_store import IntegrationsStore
from integratevibes.telegram_auth import TelegramAuthError, verify_init_data
from integratevibes.zernio_client import ZernioClient, ZernioError

ROOT = Path(__file__).resolve().parent
ACCESS_TOKEN = os.environ.get("MINIAPP_ACCESS_TOKEN", "")
HOST = os.environ.get("MINIAPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MINIAPP_PORT", "49234"))
COMPOSIO = shutil.which("composio") or str(Path.home() / ".local/bin/composio")
STATUS_CACHE_SECONDS = 30
CATALOG_CACHE_SECONDS = 3600
CATALOG_LIMIT = 2000
TOOLKIT_SLUG = re.compile(r"^[a-z0-9_]{1,80}$")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_TELEGRAM_USERS = {
    int(value.strip())
    for value in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if value.strip().isdigit()
}
ZERNIO_API_KEY = os.environ.get("ZERNIO_API_KEY", "")
ZERNIO_WEBHOOK_SECRET = os.environ.get("ZERNIO_WEBHOOK_SECRET", "")
INTEGRATIONS_DB_PATH = os.environ.get(
    "INTEGRATIONS_DB_PATH", "/var/lib/softvibes-miniapp/integrations.db"
)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://auth.softvibes.art")
ZERNIO_SERVICE = None
if ZERNIO_API_KEY:
    ZERNIO_SERVICE = IntegrationService(
        ZernioClient(ZERNIO_API_KEY),
        IntegrationsStore(INTEGRATIONS_DB_PATH),
        public_base_url=PUBLIC_BASE_URL,
    )

# Notebookvibes recommendations remain highlighted inside the complete catalog.
# Every other connectable slug must first come from Composio's live catalog.
TOOLKITS = {
    "googledrive": {
        "label": "Google Drive",
        "mark": "GD",
        "category": "Fuentes",
        "priority": 1,
        "description": "Busca y recupera documentos para convertirlos en fuentes del notebook.",
        "capabilities": ["Buscar archivos", "Leer documentos", "Importar fuentes"],
        "search_terms": ["drive", "documentos", "pdf", "archivos", "fuentes", "google"],
    },
    "notion": {
        "label": "Notion",
        "mark": "N",
        "category": "Conocimiento",
        "priority": 2,
        "description": "Consulta páginas y bases compartidas para contrastar conocimiento interno.",
        "capabilities": ["Buscar páginas", "Leer contenido", "Consultar bases"],
        "search_terms": ["notion", "wiki", "páginas", "bases", "conocimiento", "notas"],
    },
    "youtube": {
        "label": "YouTube",
        "mark": "YT",
        "category": "Investigación",
        "priority": 3,
        "description": "Descubre videos relevantes y sus metadatos para ampliar una investigación.",
        "capabilities": ["Buscar videos", "Comparar canales", "Revisar metadatos"],
        "search_terms": ["youtube", "videos", "canales", "investigación", "audio"],
    },
    "github": {
        "label": "GitHub",
        "mark": "GH",
        "category": "Investigación",
        "priority": 4,
        "description": "Analiza repositorios, documentación, issues y cambios técnicos con trazabilidad.",
        "capabilities": ["Leer repositorios", "Buscar código", "Revisar issues"],
        "search_terms": ["github", "repositorios", "código", "issues", "documentación", "técnico"],
    },
    "gmail": {
        "label": "Gmail",
        "mark": "G",
        "category": "Comunicación",
        "priority": 5,
        "description": "Encuentra conversaciones y adjuntos que contienen contexto de una investigación.",
        "capabilities": ["Buscar correos", "Leer hilos", "Localizar adjuntos"],
        "search_terms": ["gmail", "correo", "email", "mensajes", "adjuntos", "comunicación"],
    },
    "googlecalendar": {
        "label": "Google Calendar",
        "mark": "31",
        "category": "Organización",
        "priority": 6,
        "description": "Relaciona reuniones y fechas con proyectos, decisiones y seguimientos.",
        "capabilities": ["Consultar eventos", "Revisar calendarios", "Ubicar reuniones"],
        "search_terms": ["calendar", "calendario", "eventos", "reuniones", "agenda", "google"],
    },
    "slack": {
        "label": "Slack",
        "mark": "S",
        "category": "Comunicación",
        "priority": 7,
        "description": "Busca decisiones, mensajes y contexto compartido en canales de trabajo.",
        "capabilities": ["Buscar mensajes", "Leer canales", "Revisar hilos"],
        "search_terms": ["slack", "canales", "mensajes", "equipo", "hilos", "decisiones"],
    },
    "dropbox": {
        "label": "Dropbox",
        "mark": "DB",
        "category": "Fuentes",
        "priority": 8,
        "description": "Localiza archivos de investigación guardados fuera de Google Drive.",
        "capabilities": ["Buscar archivos", "Leer documentos", "Explorar carpetas"],
        "search_terms": ["dropbox", "archivos", "carpetas", "documentos", "fuentes", "nube"],
    },
}

_status_lock = threading.Lock()
_status_cache: tuple[float, dict] | None = None
_catalog_lock = threading.Lock()
_catalog_cache: tuple[float, list[dict]] | None = None


def run_composio(args: list[str], timeout: int = 60) -> dict | list:
    result = subprocess.run(
        [COMPOSIO, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Composio command failed").strip())
    raw = result.stdout.strip() or "{}"
    try:
        parsed = json.loads(raw, strict=False)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Composio returned a non-JSON response") from exc
    if not isinstance(parsed, (dict, list)):
        raise RuntimeError("Composio returned an unexpected response")
    return parsed


def get_available_toolkits(force: bool = False) -> list[dict]:
    global _catalog_cache
    now = time.monotonic()
    with _catalog_lock:
        if not force and _catalog_cache and now - _catalog_cache[0] < CATALOG_CACHE_SECONDS:
            return _catalog_cache[1]
        payload = run_composio(
            ["dev", "--mode", "on", "toolkits", "list", "--limit", str(CATALOG_LIMIT)],
            timeout=90,
        )
        if not isinstance(payload, list):
            raise RuntimeError("Composio did not return a toolkit catalog")
        deduplicated: dict[str, dict] = {}
        for raw_toolkit in payload:
            if not isinstance(raw_toolkit, dict):
                continue
            slug = str(raw_toolkit.get("slug", "")).strip().lower()
            if not TOOLKIT_SLUG.fullmatch(slug):
                continue
            deduplicated[slug] = raw_toolkit
        if not deduplicated:
            raise RuntimeError("Composio returned an empty toolkit catalog")
        catalog = list(deduplicated.values())
        _catalog_cache = (time.monotonic(), catalog)
        return catalog


def _connection_state(accounts: object) -> tuple[str, int]:
    if not isinstance(accounts, list):
        return "disconnected", 0
    states = [str(account.get("status", "UNKNOWN")).upper() for account in accounts if isinstance(account, dict)]
    if "ACTIVE" in states:
        status = "active"
    elif any(state in {"INITIALIZING", "PENDING", "INITIATED"} for state in states):
        status = "pending"
    elif states:
        status = "expired"
    else:
        status = "disconnected"
    return status, len(accounts)


def _mark_for(label: str) -> str:
    words = [word for word in re.split(r"[^A-Za-z0-9]+", label) if word]
    if not words:
        return "APP"
    return "".join(word[0] for word in words[:2]).upper()[:3]


def toolkit_catalog(connections: dict | None = None, available: list[dict] | None = None) -> list[dict]:
    connections = connections or {}
    if available is None:
        available = [
            {"slug": slug, "name": metadata["label"], "description": metadata["description"]}
            for slug, metadata in TOOLKITS.items()
        ]
    catalog = []
    seen: set[str] = set()
    for raw_toolkit in available:
        if not isinstance(raw_toolkit, dict):
            continue
        slug = str(raw_toolkit.get("slug", "")).strip().lower()
        if slug in seen or not TOOLKIT_SLUG.fullmatch(slug):
            continue
        seen.add(slug)
        recommended = TOOLKITS.get(slug)
        label = str(raw_toolkit.get("name") or (recommended or {}).get("label") or slug).strip()
        description = str(raw_toolkit.get("description") or (recommended or {}).get("description") or "Integración disponible en Composio.").strip()
        tools_count = max(0, int(raw_toolkit.get("tools_count") or 0))
        triggers_count = max(0, int(raw_toolkit.get("triggers_count") or 0))
        no_auth = bool(raw_toolkit.get("is_no_auth"))
        status, accounts = _connection_state(connections.get(slug, []))
        if no_auth and status == "disconnected":
            status = "available"
        metadata = {
            "label": label,
            "mark": (recommended or {}).get("mark") or _mark_for(label),
            "category": (recommended or {}).get("category") or "Catálogo Composio",
            "priority": (recommended or {}).get("priority", 10_000),
            "description": (recommended or {}).get("description") or description,
            "capabilities": (recommended or {}).get("capabilities") or [
                f"{tools_count} herramientas",
                f"{triggers_count} automatizaciones",
            ],
            "search_terms": (recommended or {}).get("search_terms") or [slug, label, description],
            "recommended": recommended is not None,
            "connectable": not no_auth,
            "tools_count": tools_count,
            "triggers_count": triggers_count,
        }
        catalog.append({"slug": slug, **metadata, "status": status, "accounts": accounts})
    return sorted(catalog, key=lambda item: (not item["recommended"], item["priority"], item["label"].casefold()))


def get_catalog_status(force: bool = False) -> dict:
    global _status_cache
    now = time.monotonic()
    with _status_lock:
        if not force and _status_cache and now - _status_cache[0] < STATUS_CACHE_SECONDS:
            return _status_cache[1]
        connections = run_composio(["connections", "list"])
        if not isinstance(connections, dict):
            raise RuntimeError("Composio did not return connection statuses")
        available = get_available_toolkits()
        toolkits = toolkit_catalog(connections, available)
        payload = {
            "ok": True,
            "toolkits": toolkits,
            "active": sum(item["status"] == "active" for item in toolkits),
            "recommended": sum(item["recommended"] for item in toolkits),
            "total": len(toolkits),
        }
        _status_cache = (time.monotonic(), payload)
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "ComposioMiniApp/2.0"

    def log_message(self, _format: str, *_args) -> None:
        # Avoid logging the access token embedded in the Mini App path.
        return

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org;",
        )

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return bool(ACCESS_TOKEN) and self.headers.get("X-Miniapp-Token", "") == ACCESS_TOKEN

    def telegram_user_id(self) -> int | None:
        if not TELEGRAM_BOT_TOKEN or not ALLOWED_TELEGRAM_USERS:
            return None
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        try:
            user = verify_init_data(
                init_data,
                TELEGRAM_BOT_TOKEN,
                allowed_user_ids=ALLOWED_TELEGRAM_USERS,
            )
        except (TelegramAuthError, ValueError):
            return None
        return user.id

    def read_json(self, *, max_bytes: int = 4096) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > max_bytes:
            raise ValueError("request_too_large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")
        return payload

    def require_zernio(self) -> tuple[int, IntegrationService] | None:
        user_id = self.telegram_user_id()
        if user_id is None:
            self.send_json({"ok": False, "error": "telegram_auth_required"}, 401)
            return None
        if ZERNIO_SERVICE is None:
            self.send_json({"ok": False, "error": "zernio_not_configured"}, 503)
            return None
        return user_id, ZERNIO_SERVICE

    def send_callback_page(self, *, ok: bool, title: str, detail: str) -> None:
        safe_title = escape(title)
        safe_detail = escape(detail)
        accent = "#54b887" if ok else "#d56d68"
        body = f"""<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<meta name=\"color-scheme\" content=\"light dark\"><title>{safe_title}</title>
<style>body{{font-family:system-ui;background:#0f1012;color:#f4f2ec;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}main{{max-width:420px;text-align:center}}i{{display:block;width:54px;height:54px;border-radius:50%;background:{accent};margin:0 auto 18px}}h1{{font-size:24px}}p{{color:#a7a9b0;line-height:1.5}}</style></head><body><main><i></i><h1>{safe_title}</h1><p>{safe_detail}</p><p>Ya puedes cerrar esta ventana y volver a Telegram.</p></main></body></html>""".encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json({"ok": True, "service": "composio-telegram-miniapp", "version": 3})
            return
        if path == f"/app/{ACCESS_TOKEN}" and ACCESS_TOKEN:
            body = (ROOT / "index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.security_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/app.js":
            body = (ROOT / "app.js").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.security_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            if not self.authorized():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                self.send_json(get_catalog_status())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 502)
            return
        if path == "/api/zernio/status":
            context = self.require_zernio()
            if context is None:
                return
            user_id, service = context
            try:
                self.send_json(service.status(user_id))
            except (IntegrationServiceError, ZernioError):
                self.send_json({"ok": False, "error": "zernio_status_failed"}, 502)
            return
        if path == "/integrations/zernio/callback":
            if ZERNIO_SERVICE is None:
                self.send_callback_page(
                    ok=False,
                    title="Zernio no está configurado",
                    detail="Vuelve a Telegram e inténtalo cuando la integración esté disponible.",
                )
                return
            query = parse_qs(urlparse(self.path).query)
            try:
                result = ZERNIO_SERVICE.finish_callback(
                    state=(query.get("state") or [""])[0],
                    returned_profile_id=(query.get("profileId") or [None])[0],
                    returned_platform=(query.get("connected") or query.get("platform") or [None])[0],
                    error=(query.get("error") or [None])[0],
                )
                if result["ok"]:
                    self.send_callback_page(
                        ok=True,
                        title="Cuenta conectada",
                        detail="Regresa a Telegram; Integratevibes actualizará el estado automáticamente.",
                    )
                else:
                    self.send_callback_page(
                        ok=False,
                        title="No se completó la conexión",
                        detail="Regresa a Telegram y vuelve a intentarlo.",
                    )
            except (IntegrationServiceError, ZernioError):
                self.send_callback_page(
                    ok=False,
                    title="Enlace inválido o vencido",
                    detail="Regresa a Telegram y genera una conexión nueva.",
                )
            return
        self.send_json({"ok": False, "error": "not_found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/webhooks/zernio":
            if ZERNIO_SERVICE is None:
                self.send_json({"ok": False, "error": "invalid_webhook"}, 400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 16_384:
                    raise ValueError("invalid content length")
                body = self.rfile.read(length)
                signature = self.headers.get("X-Zernio-Signature", "")
                if not verify_zernio_webhook(body, signature, ZERNIO_WEBHOOK_SECRET):
                    self.send_json({"ok": False, "error": "invalid_signature"}, 401)
                    return
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("webhook body must be an object")
                event_id = str(payload.get("id") or self.headers.get("X-Zernio-Event-Id", ""))
                if not event_id:
                    raise ValueError("missing event id")
                accepted = ZERNIO_SERVICE.store.record_webhook_event(event_id)
                self.send_json({"ok": True, "accepted": accepted})
            except (json.JSONDecodeError, TypeError, ValueError):
                self.send_json({"ok": False, "error": "invalid_webhook"}, 400)
            except sqlite3.OperationalError:
                self.send_json({"ok": False, "error": "webhook_temporarily_unavailable"}, 503)
            return

        if path in {
            "/api/zernio/connect",
            "/api/zernio/telegram/start",
            "/api/zernio/telegram/check",
        }:
            context = self.require_zernio()
            if context is None:
                return
            user_id, service = context
            try:
                payload = self.read_json()
                if path == "/api/zernio/connect":
                    result = service.create_connect(user_id, str(payload.get("platform", "")))
                elif path == "/api/zernio/telegram/start":
                    result = service.start_telegram(user_id)
                else:
                    result = service.check_telegram(user_id, str(payload.get("code", "")))
                self.send_json(result)
            except (IntegrationServiceError, ValueError):
                self.send_json({"ok": False, "error": "invalid_integration_request"}, 400)
            except ZernioError:
                self.send_json({"ok": False, "error": "zernio_request_failed"}, 502)
            return

        if path != "/api/link":
            self.send_json({"ok": False, "error": "not_found"}, 404)
            return
        if not self.authorized():
            self.send_json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length) or b"{}")
            slug = str(payload.get("toolkit", "")).strip().lower()
            if not TOOLKIT_SLUG.fullmatch(slug):
                self.send_json({"ok": False, "error": "unsupported_toolkit"}, 400)
                return
            available = {str(item.get("slug", "")).lower(): item for item in get_available_toolkits()}
            toolkit = available.get(slug)
            if not toolkit or bool(toolkit.get("is_no_auth")):
                self.send_json({"ok": False, "error": "unsupported_toolkit"}, 400)
                return
            result = run_composio(["link", slug, "--no-wait", "--no-browser"])
            if not isinstance(result, dict):
                raise RuntimeError("Composio returned an unexpected link response")
            redirect = result.get("redirect_url")
            if not redirect or not str(redirect).startswith("https://connect.composio.dev/"):
                raise RuntimeError("Composio did not return a valid authorization URL")
            global _status_cache
            _status_cache = None
            self.send_json({"ok": True, "toolkit": slug, "redirect_url": redirect})
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "invalid_json"}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 502)


if __name__ == "__main__":
    if not ACCESS_TOKEN:
        raise SystemExit("MINIAPP_ACCESS_TOKEN is required")
    if not Path(COMPOSIO).exists():
        raise SystemExit("Composio CLI not found")
    print(f"Composio Mini App listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
