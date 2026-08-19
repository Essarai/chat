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
        self.assertEqual(response.json()["guide"], "/")

    def test_guide_is_public_and_contains_both_user_manuals(self):
        with TestClient(self._app()) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("img-src 'self'", response.headers["content-security-policy"])
        self.assertIn("media-src 'self'", response.headers["content-security-policy"])
        self.assertIn("期刊知识服务", response.text)
        self.assertNotIn("关系期刊知识服务", response.text)
        self.assertIn('preload="none"', response.text)
        self.assertIn('poster="guide-demo-poster.jpg"', response.text)
        self.assertIn('data-src="guide-demo.mp4"', response.text)
        self.assertNotIn("<source", response.text)
        self.assertIn("播放演示", response.text)
        self.assertIn("作者使用手册", response.text)
        self.assertIn("编辑使用手册", response.text)
        self.assertIn("Journal Knowledge Service | Guide for Authors and Editors", response.text)
        self.assertIn('data-language="en"', response.text)
        self.assertIn("先说清任务，再让证据回答", response.text)
        self.assertIn("先固定统计口径，再比较变化", response.text)
        self.assertIn("https://journals.up.railway.app/mcp", response.text)
        self.assertIn("ZDXBNXB", response.text)
        self.assertIn("ZDXBRWB", response.text)
        self.assertIn("不代表录用概率", response.text)

    def test_legacy_guide_url_redirects_to_root(self):
        with TestClient(self._app()) as client:
            response = client.get("/guide", follow_redirects=False)

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers["location"], "/")

    def test_guide_video_is_public_and_supports_range_requests(self):
        with TestClient(self._app()) as client:
            response = client.get(
                "/guide-demo.mp4", headers={"Range": "bytes=0-1023"}
            )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(len(response.content), 1024)

    def test_guide_video_poster_is_public(self):
        with TestClient(self._app()) as client:
            response = client.get("/guide-demo-poster.jpg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertLess(len(response.content), 300_000)

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
