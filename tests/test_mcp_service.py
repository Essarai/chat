from __future__ import annotations

import unittest

from app.mcp_server.contracts import CompareSet, PublicationScope, ResearchDescription
from app.mcp_server.service import JournalMCPService


def _semantic_stub(**kwargs):
    return {
        "hits": [
            {
                "doi": "10.3785/1008-9209.2020.01.001",
                "title": "语义检索测试论文",
                "year": 2020,
                "distance": 0.2,
            }
        ],
        "queries": [kwargs.get("question", "")],
        "dropped_editorial": 0,
        "dropped_out_of_window": 0,
    }


class JournalMCPServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = JournalMCPService(semantic_searcher=_semantic_stub)
        cls.scope = PublicationScope(
            journal_id="ZDXBNXB",
            topics=["水稻"],
            year_start=2015,
            year_end=2024,
        )
        cls.research = ResearchDescription(
            title="水稻病害机器学习识别",
            abstract="本研究探讨如何利用机器学习识别水稻病害。结果表明模型有效。",
            keywords=["水稻", "病害", "机器学习"],
        )

    def test_data_scope_and_entity_resolution(self):
        scope = self.service.get_journal_data_scope("ZDXBNXB")
        self.assertEqual(scope.status, "complete")
        self.assertGreater(scope.data["record_counts"]["papers"], 0)
        self.assertIn("录用概率预测", scope.unsupported_claims)

        resolved = self.service.resolve_academic_entity(
            "topic", "水稻", "ZDXBNXB"
        )
        self.assertIn(resolved.status, {"complete", "ambiguous"})
        self.assertTrue(resolved.data["candidates"])

    def test_structured_search_details_and_pagination(self):
        result = self.service.search_papers(self.scope, limit=2, cursor=0)
        self.assertEqual(result.status, "complete")
        self.assertLessEqual(len(result.data["papers"]), 2)
        self.assertTrue(result.evidence_refs)
        doi = result.data["papers"][0]["doi"]

        details = self.service.get_paper_details(doi, "ZDXBNXB")
        self.assertEqual(details.status, "complete")
        self.assertEqual(details.data["paper"]["doi"], doi)
        self.assertIn("authors", details.data["paper"])
        self.assertIn("keywords", details.data["paper"])

    def test_semantic_search_and_feature_extraction(self):
        semantic = self.service.semantic_search_papers(
            "水稻病害识别", "ZDXBNXB", top_k=5
        )
        self.assertEqual(semantic.status, "complete")
        self.assertEqual(len(semantic.data["papers"]), 1)

        features = self.service.extract_research_features(
            self.research, "ZDXBNXB"
        )
        self.assertEqual(features.status, "complete")
        self.assertIn("水稻", features.data["topics"])
        self.assertIn("机器学习", features.data["methods"])

    def test_aggregation_trend_and_comparison(self):
        for group_by in ("year", "topic", "author", "institution"):
            result = self.service.aggregate_publications(
                self.scope, group_by, limit=10
            )
            self.assertEqual(result.status, "complete")
            self.assertGreater(result.data["total_papers"], 0)
            self.assertTrue(result.data["groups"])

        trend = self.service.analyze_publication_trend(self.scope)
        self.assertEqual(trend.status, "complete")
        self.assertIn(
            trend.data["classification"], {"up", "stable", "down"}
        )
        self.assertEqual(trend.data["formula_version"], "linear-trend-v1")

        compared = self.service.compare_publication_sets(
            CompareSet(
                label="前期",
                scope=PublicationScope(
                    journal_id="ZDXBNXB", year_start=2015, year_end=2019
                ),
            ),
            CompareSet(
                label="近期",
                scope=PublicationScope(
                    journal_id="ZDXBNXB", year_start=2020, year_end=2024
                ),
            ),
            topic_limit=10,
        )
        self.assertEqual(compared.status, "complete")
        self.assertTrue(compared.data["topic_changes"])

    def test_contributor_rank_profile_and_network(self):
        for basis in (
            "publication_count",
            "continuity",
            "topic_coverage",
            "collaboration",
        ):
            ranked = self.service.rank_contributors(
                "author", self.scope, basis, limit=5
            )
            self.assertEqual(ranked.status, "complete")
            self.assertEqual(ranked.data["ranking_basis"], basis)

        searched = self.service.search_papers(self.scope, limit=1)
        doi = searched.data["papers"][0]["doi"]
        details = self.service.get_paper_details(doi, "ZDXBNXB")
        author = details.data["paper"]["authors"][0]
        author_name = author.get("name_zh") or author.get("name_en")
        profile = self.service.get_contributor_profile(
            "author", author_name, "ZDXBNXB"
        )
        self.assertEqual(profile.status, "complete")

        network = self.service.get_collaboration_network(
            "topic", "ZDXBNXB", topic="水稻", limit=5
        )
        self.assertEqual(network.status, "complete")
        self.assertTrue(network.evidence_refs)

    def test_fit_and_recommendation_preserve_business_boundary(self):
        fit = self.service.assess_research_fit(
            self.research, "ZDXBNXB", 2015, 2024
        )
        self.assertEqual(fit.status, "complete")
        self.assertIn(fit.data["fit_band"], {"weak", "moderate", "strong"})
        self.assertIn("录用概率", fit.unsupported_claims)
        self.assertEqual(fit.data["score_version"], "journal-history-fit-v1")

        recommended = self.service.rank_recommended_papers(
            self.research, "ZDXBNXB", limit=5
        )
        self.assertEqual(recommended.status, "complete")
        self.assertTrue(recommended.data["recommended_papers"])
        self.assertTrue(recommended.evidence_refs)

    def test_article_type_is_explicitly_partial(self):
        result = self.service.search_papers(
            PublicationScope(journal_id="ZDXBNXB", article_type="综述"),
            limit=1,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.limitations)


if __name__ == "__main__":
    unittest.main()
