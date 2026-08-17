from __future__ import annotations

import unittest

from starlette.testclient import TestClient

from app.mcp_server.server import build_server, create_http_app
from app.mcp_server.service import JournalMCPService


class MCPHTTPTests(unittest.TestCase):
    def _app(self, token: str = "test-secret"):
        service = JournalMCPService(
            semantic_searcher=lambda **kwargs: {"hits": [], "queries": []}
        )
        return create_http_app(
            build_server(service),
            bearer_token=token,
            host="127.0.0.1",
        )

    def test_health_is_public(self):
        with TestClient(self._app()) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(response.json()["guide"], "/guide")

    def test_guide_is_public_and_contains_both_user_manuals(self):
        with TestClient(self._app()) as client:
            response = client.get("/guide")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("作者使用手册", response.text)
        self.assertIn("编辑使用手册", response.text)
        self.assertIn("https://journals.up.railway.app/mcp", response.text)
        self.assertIn("ZDXBNXB", response.text)
        self.assertIn("ZDXBRWB", response.text)
        self.assertIn("不代表录用概率", response.text)

    def test_mcp_endpoint_rejects_missing_or_wrong_token(self):
        with TestClient(self._app()) as client:
            missing = client.post("/mcp", json={})
            wrong = client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer wrong-secret"},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")

    def test_http_mode_fails_closed_without_token(self):
        with self.assertRaisesRegex(RuntimeError, "MCP_BEARER_TOKEN"):
            create_http_app(build_server(), bearer_token=None)


if __name__ == "__main__":
    unittest.main()
