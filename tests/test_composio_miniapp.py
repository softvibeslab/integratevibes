from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "composio-telegram-miniapp" / "server.py"
SPEC = importlib.util.spec_from_file_location("composio_miniapp_server", SERVER_PATH)
assert SPEC and SPEC.loader
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        server._status_cache = None
        server._catalog_cache = None

    @staticmethod
    def available_toolkits() -> list[dict]:
        return [
            {"slug": slug, "name": metadata["label"], "description": metadata["description"]}
            for slug, metadata in server.TOOLKITS.items()
        ]

    def test_catalog_is_curated_and_prioritized(self) -> None:
        catalog = server.toolkit_catalog({})
        self.assertEqual(catalog[0]["slug"], "googledrive")
        self.assertEqual(len(catalog), 8)
        self.assertEqual({item["slug"] for item in catalog}, set(server.TOOLKITS))
        self.assertTrue(all(item["description"] for item in catalog))
        self.assertTrue(all(item["capabilities"] for item in catalog))

    def test_connection_states_keep_active_when_old_accounts_expired(self) -> None:
        status, accounts = server._connection_state([
            {"status": "EXPIRED"},
            {"status": "ACTIVE"},
        ])
        self.assertEqual(status, "active")
        self.assertEqual(accounts, 2)

    def test_status_uses_one_composio_call_and_cache(self) -> None:
        connections = {
            "gmail": [{"status": "ACTIVE"}],
            "notion": [{"status": "EXPIRED"}],
        }
        with (
            mock.patch.object(server, "run_composio", return_value=connections) as run,
            mock.patch.object(server, "get_available_toolkits", return_value=self.available_toolkits()),
        ):
            first = server.get_catalog_status()
            second = server.get_catalog_status()
        run.assert_called_once_with(["connections", "list"])
        self.assertIs(first, second)
        self.assertEqual(first["active"], 1)
        self.assertEqual(first["total"], 8)

    def test_status_lock_collapses_concurrent_refreshes(self) -> None:
        barrier = threading.Barrier(4)
        results: list[dict] = []

        def fake_run(_args: list[str]) -> dict:
            return {"gmail": [{"status": "ACTIVE"}]}

        def worker() -> None:
            barrier.wait()
            results.append(server.get_catalog_status())

        with (
            mock.patch.object(server, "run_composio", side_effect=fake_run) as run,
            mock.patch.object(server, "get_available_toolkits", return_value=self.available_toolkits()),
        ):
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(results), 3)

    def test_complete_catalog_merges_recommendations_and_other_apps(self) -> None:
        available = self.available_toolkits() + [
            {
                "slug": "airtable",
                "name": "Airtable",
                "description": "Collaborative database",
                "tools_count": 25,
                "triggers_count": 6,
                "is_no_auth": False,
            },
            {
                "slug": "hackernews",
                "name": "Hacker News",
                "description": "Technology news",
                "tools_count": 14,
                "triggers_count": 0,
                "is_no_auth": True,
            },
        ]
        catalog = server.toolkit_catalog({}, available)
        by_slug = {item["slug"]: item for item in catalog}
        self.assertEqual(len(catalog), 10)
        self.assertTrue(by_slug["gmail"]["recommended"])
        self.assertFalse(by_slug["airtable"]["recommended"])
        self.assertTrue(by_slug["airtable"]["connectable"])
        self.assertFalse(by_slug["hackernews"]["connectable"])
        self.assertEqual(by_slug["hackernews"]["status"], "available")

    def test_frontend_has_search_and_no_embedded_secret(self) -> None:
        html = (ROOT / "composio-telegram-miniapp" / "index.html").read_text()
        javascript = (ROOT / "composio-telegram-miniapp" / "app.js").read_text()
        self.assertIn('id="integration-search"', html)
        self.assertIn('type="search"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertNotIn("MINIAPP_ACCESS_TOKEN", html + javascript)
        self.assertIn('fetch("/api/status"', javascript)

    def test_catalog_serializes_as_json(self) -> None:
        json.dumps(server.toolkit_catalog({}), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
