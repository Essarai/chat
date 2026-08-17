# 期刊知识服务 Agent 使用指南

本文档供连接期刊知识服务 MCP 的通用 Agent 阅读。它描述业务边界和调用策略，不包含数据库实现或部署密钥。

## 1. 服务定位

本 MCP 提供期刊论文检索、研究主题分析、作者与机构画像、合作关系、发文趋势、投稿内容匹配和相关阅读推荐能力。

Agent 负责：

- 理解用户意图并选择期刊范围；
- 规划一个或多个 MCP 工具调用；
- 检查工具状态、证据和限制；
- 将结果组织为对作者或编辑有用的自然语言答案。

MCP 负责只读查询和确定性计算。不得要求或尝试 SQL、Cypher、向量库原始查询及数据写入。

## 2. 期刊范围

| `journal_id` | 业务范围 |
|---|---|
| `ZDXBNXB` | 农业与生命科学版 |
| `ZDXBRWB` | 人文社会科学版 |

每次调用都应明确传入 `journal_id`。用户没有说明期刊时：

1. 能从研究主题可靠判断时，选择相应期刊并在答案中说明；
2. 无法可靠判断时，先询问用户；
3. 跨刊问题分别调用两个期刊，不能混合统计口径。

## 3. 核心使用原则

1. 不确定覆盖年份或数据源是否可用时，先调用 `get_journal_data_scope`。
2. 作者、机构、论文或主题名称存在别名、重名或模糊输入时，先调用 `resolve_academic_entity`。
3. 作者的业务标识符始终使用作者姓名。将 `resolve_academic_entity` 返回的姓名 `identifier` 传给画像或合作网络工具，不使用数据库内部作者 ID。
4. 有明确年份、作者、机构、主题或 DOI 条件时使用 `search_papers`；基于标题、摘要或研究描述寻找相似论文时使用 `semantic_search_papers`。
5. 需要单篇论文的摘要、作者、机构或关键词详情时，对检索结果中的 DOI 调用 `get_paper_details`。
6. 只使用工具返回的证据作答，不根据常识补造本刊数据、论文、引用、作者关系或统计结论。
7. 论文结果存在 URL 时优先返回可点击的在线 URL，同时保留标题、作者、年份和 DOI。
8. 投稿匹配只表示研究内容与本刊历史内容的适配程度，不代表录用概率、学术质量、审稿结论或全球创新性。
9. 合作网络表示本刊论文中的共同署名或共现关系，不代表导师关系、团队归属或现实组织关系。

## 4. 工具选择

| 用户目标 | 首选工具 |
|---|---|
| 确认数据覆盖和能力边界 | `get_journal_data_scope` |
| 消除作者、机构、论文或主题歧义 | `resolve_academic_entity` |
| 按明确条件查论文 | `search_papers` |
| 按研究描述查相似论文 | `semantic_search_papers` |
| 查看单篇论文详情 | `get_paper_details` |
| 从稿件描述抽取研究特征 | `extract_research_features` |
| 按年、主题、作者或机构统计 | `aggregate_publications` |
| 判断发文趋势 | `analyze_publication_trend` |
| 比较两个时期或群体 | `compare_publication_sets` |
| 排序作者或机构 | `rank_contributors` |
| 查看作者或机构本刊画像 | `get_contributor_profile` |
| 查看共同署名或合作证据 | `get_collaboration_network` |
| 判断稿件内容适配度 | `assess_research_fit` |
| 推荐本刊相关阅读 | `rank_recommended_papers` |

## 5. 推荐调用链

### 5.1 论文检索与问答

```text
必要时 get_journal_data_scope
→ search_papers 或 semantic_search_papers
→ 必要时 get_paper_details
→ 汇总论文及在线 URL
```

### 5.2 作者或机构分析

```text
resolve_academic_entity
→ get_contributor_profile
→ 必要时 get_collaboration_network
```

若解析结果为多个同名候选，不要默认选择第一项，应展示候选并请求用户确认。

### 5.3 投稿内容匹配

```text
get_journal_data_scope
→ extract_research_features
→ semantic_search_papers
→ assess_research_fit
→ rank_recommended_papers
```

最终回答应区分“历史内容匹配证据”和“本服务无法判断的录用、质量与创新性结论”。

### 5.4 主题与趋势分析

```text
aggregate_publications
→ analyze_publication_trend
→ 必要时 compare_publication_sets
→ 必要时 rank_contributors
```

不要从展示用的有限论文列表自行推算总量或趋势，应采用聚合与趋势工具返回的统计口径。

## 6. 返回状态处理

所有工具都返回统一结构，包括：

- `status`：执行状态；
- `scope`：本次结果的期刊、年份和筛选范围；
- `data`：业务结果；
- `evidence_refs`：论文、统计、实体或数据范围证据；
- `assumptions`：计算或解析假设；
- `limitations`：结果限制；
- `missing_inputs`：缺失输入；
- `unsupported_claims`：该工具不能支持的结论；
- `data_as_of`：数据时间；
- `next_cursor`：分页游标。

按以下方式处理 `status`：

| 状态 | Agent 行为 |
|---|---|
| `complete` | 基于证据正常回答 |
| `partial` | 给出现有结果并明确缺失或降级项 |
| `ambiguous` | 展示候选或请求用户澄清，不擅自选定 |
| `unsupported` | 明确说明超出能力或数据范围，不编造替代答案 |
| `error` | 简要说明失败；可调整合法参数重试一次，仍失败则停止 |

使用 `next_cursor` 继续分页，不能通过扩大 `limit` 绕过工具限制。

## 7. 回答规范

最终答案应优先包含：

1. 直接结论；
2. 查询期刊、年份和筛选范围；
3. 支撑结论的统计或代表论文；
4. 论文的标题、作者、年份、DOI 和在线 URL（工具返回时）；
5. 关键假设、数据缺口和不能支持的结论。

如果没有找到结果，应说明“在本次期刊数据范围内未找到”，而不是断言相关论文、作者或研究在现实世界中不存在。
