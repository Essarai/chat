from __future__ import annotations

import unittest

from app.mcp_server.server import build_server
from app.mcp_server.service import JournalMCPService


EXPECTED_TOOLS = {
    "get_journal_data_scope",
    "resolve_academic_entity",
    "search_papers",
    "semantic_search_papers",
    "get_paper_details",
    "extract_research_features",
    "aggregate_publications",
    "analyze_publication_trend",
    "compare_publication_sets",
    "rank_contributors",
    "get_contributor_profile",
    "get_collaboration_network",
    "assess_research_fit",
    "rank_recommended_papers",
}


class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service = JournalMCPService(
            semantic_searcher=lambda **kwargs: {"hits": [], "queries": []}
        )
        self.server = build_server(service)

    async def test_all_business_tools_are_discoverable_and_structured(self):
        tools = await self.server.list_tools()
        self.assertEqual({tool.name for tool in tools}, EXPECTED_TOOLS)
        for tool in tools:
            self.assertIsNotNone(tool.input_schema)
            self.assertIsNotNone(tool.output_schema)
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertTrue(tool.annotations.idempotent_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertFalse(tool.annotations.open_world_hint)

    async def test_scope_tool_returns_structured_content(self):
        result = await self.server.call_tool(
            "get_journal_data_scope", {"journal_id": "ZDXBNXB"}
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "complete")
        self.assertIn("evidence_refs", result.structured_content)
        self.assertIn("unsupported_claims", result.structured_content)


if __name__ == "__main__":
    unittest.main()
