# 期刊知识服务 MCP

该 MCP 将仓库已有的 SQLite、Chroma 和 Neo4j 能力封装为只读的期刊业务语义工具。通用 Agent（例如 Codex）负责理解问题、规划工具调用和组织答案；MCP 不提供 Planner、Executor、SQL、Cypher 或向量库原始查询。

## 工具

| 工具 | 用途 |
|---|---|
| `get_journal_data_scope` | 覆盖年份、数据源和能力边界 |
| `resolve_academic_entity` | 解析作者、机构、论文和主题 |
| `search_papers` | 按结构化条件检索论文 |
| `semantic_search_papers` | 按研究描述检索相似论文 |
| `get_paper_details` | 获取论文、作者、机构和关键词详情 |
| `extract_research_features` | 把稿件描述转换为结构化研究特征 |
| `aggregate_publications` | 按年、主题、作者或机构聚合 |
| `analyze_publication_trend` | 按固定公式判断发文趋势 |
| `compare_publication_sets` | 比较两个时期或论文集的主题变化 |
| `rank_contributors` | 按明确业务口径排序作者或机构 |
| `get_contributor_profile` | 获取作者或机构的本刊画像 |
| `get_collaboration_network` | 查询带共同论文证据的合作关系 |
| `assess_research_fit` | 评估研究与本刊历史内容的匹配 |
| `rank_recommended_papers` | 推荐并解释本刊相关阅读 |

所有工具统一返回 `status`、`scope`、`data`、`evidence_refs`、`assumptions`、`limitations`、`missing_inputs`、`unsupported_claims`、`data_as_of` 和 `next_cursor`。投稿匹配只表示与本刊历史内容的适配程度，不表示录用概率、学术质量或全球创新性。

## 安装

MCP SDK 2.0 要求 Python 3.10 或更高版本；生产镜像当前使用 Python 3.11。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-mcp.txt
```

项目已经提供 `.codex/config.toml`。信任并重新打开项目后，Codex 会使用 `.venv/bin/python -m app.mcp_server` 启动 STDIO MCP。在 Codex 中输入 `/mcp` 可以检查 `journal_knowledge` 及其 14 个工具。

也可以手动配置：

```bash
codex mcp add journal_knowledge -- .venv/bin/python -m app.mcp_server
```

## 运行

STDIO（默认，供 Codex 本地连接）：

```bash
.venv/bin/python -m app.mcp_server
```

Streamable HTTP（供远程 Agent 或后续插件连接）：

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 MCP_PORT=8090 \
  .venv/bin/python -m app.mcp_server
```

默认地址是 `http://127.0.0.1:8090/mcp`。若部署到公网，必须在反向代理或 MCP 层增加 Bearer Token/OAuth；当前 MVP 服务本身不实现公网认证。

## 示例编排

作者询问“我的研究是否适合本刊”：

```text
get_journal_data_scope
→ extract_research_features
→ semantic_search_papers
→ assess_research_fit
→ rank_recommended_papers
```

编辑询问“近十年主题如何变化”：

```text
aggregate_publications
→ analyze_publication_trend
→ compare_publication_sets
→ rank_contributors
```

## 测试

```bash
.venv/bin/python -m unittest tests.test_mcp_service tests.test_mcp_protocol
```

测试使用真实 SQLite 语料和本地语义检索桩，不访问 Chroma 或 Neo4j 网络服务。
