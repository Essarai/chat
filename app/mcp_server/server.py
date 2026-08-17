from __future__ import annotations

import os
from typing import Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.config import DEFAULT_JOURNAL_ID
from app.mcp_server.contracts import (
    CompareSet,
    EntityType,
    JournalId,
    PublicationScope,
    ResearchDescription,
    ToolResponse,
)
from app.mcp_server.service import JournalMCPService


SERVER_INSTRUCTIONS = """期刊知识服务的只读业务语义工具。通用 Agent 负责理解问题、规划和组织答案；本服务器只负责可靠查询、确定性计算和证据返回。回答作者问题时，不得把历史内容匹配解释为录用概率或学术质量；回答趋势、排名和合作关系时必须引用工具返回的统计或 DOI 证据。数据不足时保留 partial、ambiguous 或 unsupported 状态，不要自行补造结论。不要尝试通过这些工具执行 SQL、Cypher 或向量库原始查询。"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def build_server(service: Optional[JournalMCPService] = None) -> MCPServer:
    journal = service or JournalMCPService()
    mcp = MCPServer(
        name="journal-knowledge-service",
        title="期刊知识服务",
        description="面向作者和编辑的论文、主题、趋势、贡献者、合作关系与投稿匹配工具。",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
    )

    @mcp.tool(
        title="获取期刊数据边界",
        description="在回答期刊问题前确认覆盖年份、可用数据源和不支持的结论。不要用它检索具体论文。",
        annotations=READ_ONLY,
    )
    def get_journal_data_scope(
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
    ) -> ToolResponse:
        return journal.get_journal_data_scope(journal_id)

    @mcp.tool(
        title="解析学术实体",
        description="将作者、机构、论文或主题名称解析为本刊数据中的稳定实体候选。存在同名或模糊输入时先调用；不要把第一个模糊候选直接当作唯一实体。",
        annotations=READ_ONLY,
    )
    def resolve_academic_entity(
        entity_type: EntityType,
        text: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        limit: int = 10,
    ) -> ToolResponse:
        return journal.resolve_academic_entity(entity_type, text, journal_id, limit)

    @mcp.tool(
        title="结构化检索论文",
        description="按年份、主题、作者、机构或 DOI 检索本刊论文并分页返回。适合明确条件检索；自然语言相似性问题应使用 semantic_search_papers。",
        annotations=READ_ONLY,
    )
    def search_papers(
        scope: PublicationScope,
        limit: int = 20,
        cursor: int = 0,
    ) -> ToolResponse:
        return journal.search_papers(scope, limit, cursor)

    @mcp.tool(
        title="语义检索相似论文",
        description="根据标题、摘要或研究描述查找语义相似的本刊论文。适合相似论文和邻近主题发现；明确年份、作者或机构筛选优先使用 search_papers。",
        annotations=READ_ONLY,
    )
    def semantic_search_papers(
        query: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        top_k: int = 10,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        return journal.semantic_search_papers(
            query, journal_id, top_k, year_start, year_end
        )

    @mcp.tool(
        title="获取论文详情",
        description="按 DOI 获取论文元数据、摘要、作者、机构和关键词。仅用于本刊已收录论文；不要用它搜索未知 DOI。",
        annotations=READ_ONLY,
    )
    def get_paper_details(
        doi: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
    ) -> ToolResponse:
        return journal.get_paper_details(doi, journal_id)

    @mcp.tool(
        title="抽取研究特征",
        description="把稿件标题、摘要和关键词转换为主题、研究对象、方法、研究问题与结论等稳定结构，供后续投稿匹配和论文推荐使用。它不评价学术质量。",
        annotations=READ_ONLY,
    )
    def extract_research_features(
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
    ) -> ToolResponse:
        return journal.extract_research_features(research, journal_id)

    @mcp.tool(
        title="聚合期刊发文",
        description="对指定论文范围按年份、主题、作者或机构做去重论文统计，返回占比和集中度。适合统计口径；不要让 Agent 自行从展示列表推算总量。",
        annotations=READ_ONLY,
    )
    def aggregate_publications(
        scope: PublicationScope,
        group_by: Literal["year", "topic", "author", "institution"],
        limit: int = 50,
    ) -> ToolResponse:
        return journal.aggregate_publications(scope, group_by, limit)

    @mcp.tool(
        title="分析发文趋势",
        description="按固定公式计算年度序列、同比、斜率和上升/稳定/下降分类。趋势判断必须使用该工具的公式与阈值，不能由 Agent 临时编造。",
        annotations=READ_ONLY,
    )
    def analyze_publication_trend(scope: PublicationScope) -> ToolResponse:
        return journal.analyze_publication_trend(scope)

    @mcp.tool(
        title="比较两个论文集",
        description="比较同一期刊内两个时间段、主题或群体的规模和主题结构变化，返回新增、消失、增强和减弱主题及代表论文。",
        annotations=READ_ONLY,
    )
    def compare_publication_sets(
        set_a: CompareSet,
        set_b: CompareSet,
        topic_limit: int = 30,
    ) -> ToolResponse:
        return journal.compare_publication_sets(set_a, set_b, topic_limit)

    @mcp.tool(
        title="贡献者排序",
        description="按发文量、持续性、主题覆盖或合作强度对作者或机构排序，并明确排序依据。不要将排名解释为学术质量或审稿资格。",
        annotations=READ_ONLY,
    )
    def rank_contributors(
        entity_type: Literal["author", "institution"],
        scope: PublicationScope,
        ranking_basis: Literal[
            "publication_count", "continuity", "topic_coverage", "collaboration"
        ] = "publication_count",
        limit: int = 10,
    ) -> ToolResponse:
        return journal.rank_contributors(
            entity_type, scope, ranking_basis, limit
        )

    @mcp.tool(
        title="获取作者或机构画像",
        description="获取作者或机构在本刊中的论文、主题和年度活动画像。先用 resolve_academic_entity 消除同名歧义；画像范围仅代表本刊数据。",
        annotations=READ_ONLY,
    )
    def get_contributor_profile(
        entity_type: Literal["author", "institution"],
        identifier: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        return journal.get_contributor_profile(
            entity_type, identifier, journal_id, year_start, year_end
        )

    @mcp.tool(
        title="获取合作网络",
        description="查询作者、机构或主题范围内的合作节点和边，并返回共同论文证据。不要把同现关系解释为导师、团队归属或现实组织关系。",
        annotations=READ_ONLY,
    )
    def get_collaboration_network(
        entity_type: Literal["author", "institution", "topic"],
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        identifier: Optional[str] = None,
        topic: Optional[str] = None,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
        limit: int = 30,
    ) -> ToolResponse:
        return journal.get_collaboration_network(
            entity_type,
            journal_id,
            identifier,
            topic,
            year_start,
            year_end,
            limit,
        )

    @mcp.tool(
        title="评估研究与期刊历史内容的匹配",
        description="根据稿件研究特征和本刊历史主题证据返回弱/中/强匹配。只能用于投稿前内容适配判断，不能输出录用概率、学术质量或全球创新性结论。",
        annotations=READ_ONLY,
    )
    def assess_research_fit(
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        return journal.assess_research_fit(
            research, journal_id, year_start, year_end
        )

    @mcp.tool(
        title="推荐本刊论文",
        description="围绕研究描述和阅读目标推荐本刊论文，返回排序依据、推荐理由和 DOI 证据。不要推荐本刊数据以外的论文或伪造引用。",
        annotations=READ_ONLY,
    )
    def rank_recommended_papers(
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        goal: Literal[
            "submission_preparation", "topic_learning", "method_reference"
        ] = "submission_preparation",
        limit: int = 10,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        return journal.rank_recommended_papers(
            research, journal_id, goal, limit, year_start, year_end
        )

    return mcp


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    server = build_server()
    if transport == "streamable-http":
        server.run(
            "streamable-http",
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MCP_PORT", "8090")),
            streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
            stateless_http=True,
            json_response=True,
        )
        return
    if transport != "stdio":
        raise ValueError("MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    server.run("stdio")


if __name__ == "__main__":
    main()
