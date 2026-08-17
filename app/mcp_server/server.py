from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import Receive, Scope, Send

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


GUIDE_HTML = Path(__file__).with_name("guide.html").read_text(encoding="utf-8")

SERVER_INSTRUCTIONS = """你连接的是只读的期刊知识服务。你负责理解用户问题、选择工具、规划调用顺序并根据证据组织答案；本服务器只负责可靠查询、确定性计算和证据返回。

使用规则：
1. 每次调用都明确 journal_id：ZDXBNXB 是农业与生命科学版，ZDXBRWB 是人文社会科学版。用户未说明且无法从语境判断时，先询问期刊；跨刊问题应分别调用并分别说明范围。
2. 不确定数据边界时先调用 get_journal_data_scope；实体可能重名或输入模糊时先调用 resolve_academic_entity。作者业务参数始终使用作者姓名，不使用数据库内部作者 ID。
3. 明确年份、作者、机构、主题或 DOI 条件时使用 search_papers；自然语言相似性问题使用 semantic_search_papers。需要单篇完整信息时再按 DOI 调用 get_paper_details。
4. 投稿匹配通常按 extract_research_features、semantic_search_papers、assess_research_fit、rank_recommended_papers 的顺序组合；趋势问题通常组合 aggregate_publications、analyze_publication_trend 和 compare_publication_sets。
5. 只依据工具返回的 data、evidence_refs、scope、assumptions 和 limitations 作答。论文结果可用时优先呈现标题、作者、年份、DOI 和在线 URL；趋势、排名与合作关系必须引用返回的统计或论文证据。
6. 不得把历史内容匹配解释为录用概率、学术质量或全球创新性；不得把共现关系解释为导师关系、团队归属或现实组织关系。
7. status 为 partial、ambiguous、unsupported 或 error 时，应向用户说明缺失信息或限制，必要时补充调用或请求澄清，不得补造结论。
8. 不要尝试执行 SQL、Cypher、向量库原始查询或任何写入操作。
作者和编辑的公开使用手册位于当前服务的 /guide。用户询问连接方法、示例问题或结果边界时，可以引导其查看该页面。"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class StaticBearerAuthMiddleware:
    """Protect the MCP endpoint with one deployment secret.

    This intentionally implements static bearer-token authentication rather
    than an OAuth authorization flow. It is suitable for the MVP's
    server-to-server Codex connection and can later be replaced by OAuth
    without changing the business tools.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        token: Optional[str],
        protected_path: str,
        allow_insecure: bool = False,
    ) -> None:
        if not token and not allow_insecure:
            raise RuntimeError(
                "MCP_BEARER_TOKEN is required for Streamable HTTP. "
                "Set MCP_ALLOW_INSECURE_HTTP=true only for local development."
            )
        self.app = app
        self.token = token
        self.protected_path = protected_path.rstrip("/") or "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        is_protected = path == self.protected_path or path.startswith(
            f"{self.protected_path}/"
        )
        if scope["type"] == "http" and is_protected and self.token:
            authorization = ""
            for name, value in scope.get("headers", []):
                if name.lower() == b"authorization":
                    authorization = value.decode("latin-1")
                    break
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(authorization, expected):
                body = json.dumps(
                    {
                        "error": "invalid_token",
                        "error_description": "A valid Bearer token is required",
                    }
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"www-authenticate", b"Bearer"),
                            (b"cache-control", b"no-store"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        description="将作者、机构、论文或主题名称解析为本刊数据中的业务候选。作者候选的 identifier 是姓名，不返回或使用数据库内部作者 ID；存在同名或模糊输入时不要把第一个候选直接当作唯一实体。",
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
        description="获取作者或机构在本刊中的论文、主题和年度活动画像。作者 identifier 应使用 resolve_academic_entity 返回的姓名 identifier，不使用内部作者 ID；画像范围仅代表本刊数据。",
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
        description="查询作者、机构或主题范围内的合作节点和边，并返回共同论文证据。作者 identifier 使用姓名，不使用内部作者 ID；不要把同现关系解释为导师、团队归属或现实组织关系。",
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


def create_http_app(
    server: Optional[MCPServer] = None,
    *,
    bearer_token: Optional[str] = None,
    allow_insecure: bool = False,
    host: str = "127.0.0.1",
    mcp_path: str = "/mcp",
) -> StaticBearerAuthMiddleware:
    """Create the authenticated Streamable HTTP ASGI application."""
    if not mcp_path.startswith("/"):
        raise ValueError("MCP_PATH must start with '/'")

    mcp = server or build_server()

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health_check(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "journal-knowledge-service",
                "transport": "streamable-http",
                "guide": "/guide",
            }
        )

    @mcp.custom_route("/guide", methods=["GET"], include_in_schema=False)
    async def public_guide(_request: Request) -> HTMLResponse:
        return HTMLResponse(
            GUIDE_HTML,
            headers={
                "Cache-Control": "public, max-age=300",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    app = mcp.streamable_http_app(
        streamable_http_path=mcp_path,
        stateless_http=True,
        json_response=True,
        host=host,
    )
    return StaticBearerAuthMiddleware(
        app,
        token=bearer_token,
        protected_path=mcp_path,
        allow_insecure=allow_insecure,
    )


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    server = build_server()
    if transport == "streamable-http":
        import uvicorn

        host = os.getenv("MCP_HOST", "127.0.0.1")
        port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8090")))
        app = create_http_app(
            server,
            bearer_token=os.getenv("MCP_BEARER_TOKEN"),
            allow_insecure=_env_flag("MCP_ALLOW_INSECURE_HTTP"),
            host=host,
            mcp_path=os.getenv("MCP_PATH", "/mcp"),
        )
        uvicorn.run(app, host=host, port=port)
        return
    if transport != "stdio":
        raise ValueError("MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    server.run("stdio")


if __name__ == "__main__":
    main()
