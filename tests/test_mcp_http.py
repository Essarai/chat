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
