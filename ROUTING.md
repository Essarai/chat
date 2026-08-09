# 问答路由设计

两刊（农生 `ZDXBNXB` / 人文 `ZDXBRWB`）共用同一套路由。  
`journal_id` 只切换 SQLite / Chroma / Neo4j 语料，不改变策略规则。

## 总流程

```
extract（正则 + LLM 实体）
  → query_understand（Intent Schema）
  → router（schema plan 优先；低置信 → regex 兜底）
  → analysis_planner（分析子目标）
  → simple_exec（单源）或 ReAct（多跳）
  → synthesize（模板优先，否则 LLM 润色）
```

原则：

1. **可一步 SQL 算清的不进 ReAct**（排名、趋势、主题作者榜等）。
2. **路由定「查什么」，LLM 主要「怎么说」**；名单类优先模板，降低编造。
3. **新问法优先加 Intent Schema 映射 + sql_ops**，避免无限膨胀 named task。
4. **办刊通告 / 校史噪声**靠清洗与规则规避，不靠 RAG 硬扛统计题。

关闭 Query Understanding（仅 regex 兜底）可设环境变量：`ENABLE_QUERY_UNDERSTAND=0`。

---

## Intent Schema（主路径）

模块：`app/agents/intent_schema.py`、`query_understand.py`、`capability_planner.py`。

### 字段

| 字段 | 封闭取值 / 说明 |
|---|---|
| `entity` | `paper \| author \| institution \| topic \| journal` |
| `operation` | `search \| rank \| trend \| compare \| summarize \| recommend \| profile \| coverage` |
| `goal` | `research_analysis \| submission_fit \| inventory \| refuse` |
| `sources` | `sql \| kg \| rag`（可多选） |
| `topic[]` | 短主题词 |
| `author_name` / `author_name_b` | 人名 |
| `institution` | 机构名 |
| `time_range` | `{start, end}` 或 `{last_n}` |
| `metric` | `publication_count \| keyword_freq \| …` |
| `top_n` | 1–50 |
| `confidence` | 0–1；**≥ 0.7** 才走 schema plan |
| `legacy_task` | 可选，仅日志/兼容 |

### Schema → query_plan 映射

| schema 组合 | query_plan |
|---|---|
| author + rank，无 topic | `top_authors` |
| author + rank + topic | `keyword_authors` / `authors_by_keyword` |
| institution + rank | `top_institutions` |
| institution + summarize/profile（含作者/成果） | `institution_authors` |
| topic/journal + trend | `yearly_growth`（可带 `top_keywords`） |
| topic + compare | `hotspot_compare` |
| coverage 或 `goal=submission_fit` | `submission_fit`（或 `topic_coverage`） |
| author + profile | `author_profile` |
| 多 source / 复杂组合 | `complexity=complex` → ReAct |

`route_reason` 前缀：`schema:` 或 `regex:`，便于对照。

---

## Regex 兜底（低置信 / QU 关闭）

按 `_simple_match` 优先级（先匹配先生效）：

| task | 源 | 覆盖问法示例 |
|---|---|---|
| `unsupported_citations` | SQL 拒答 | 高被引、被引次数、citation |
| `top_directions_with_papers` | SQL | 本刊主要研究方向有哪些 |
| `institution_authors` | SQL | 浙江大学相关作者有哪些代表性成果 |
| `keyword_authors` | SQL | 近5年研究过水稻发文量前十的作者 |
| `top_authors` | SQL | 近5年发文量前十的作者有哪些 |
| `top_institutions` | SQL | 发文量前十的机构 |
| `hotspot_compare` | SQL | 近10年与前10年研究热点变化 |
| `submission_fit` | SQL | 适合投 AI/基因编辑吗；是否适合投稿 |
| `topic_coverage` | SQL | 研究方向为 X 是否有相关论文（非投稿话术） |
| `author_profile` | SQL | 徐建明研究轨迹；某某全部发文 |
| `yearly_growth` | SQL | 近十年发文趋势和热门关键词 |
| `coauthored_papers` | SQL | 徐建明和施加春合作的发文有哪些 |
| `generic` | RAG | 有哪些相关研究 / 文献（且无统计类线索） |

### 软兜底

| 线索 | 落到 |
|---|---|
| 「机构」 | `top_institutions` |
| 「作者」+ 前 / 排名 / 发文量 | `top_authors` |
| 「热点」 | `hotspot_compare` |
| 「发文趋势 / 热门关键词」 | `yearly_growth` |
| 「识别 / 演变 / 对比…」且无 simple | 升 **complex** |
| 其余 | 默认 **RAG** `generic` |

---

## submission_fit（投稿决策）

扩展现有专题覆盖，不另起炉灶：

- SQL：`topic_keyword_stats` + `papers_by_keyword`
- 额外字段：`coverage_summary`、`sample_papers`、`fit_label`（`strong|moderate|weak`）
- 模板：`try_submission_fit_template`（覆盖结论 → 热词/篇数 → 相似论文 → 投稿注意）
- SQL 命中 `weak` 时，simple_exec 可补 **1 次** RAG；名单仍以 SQL 为准

农生/人文共用同一 schema；topic 由 QU 填槽（如「基因编辑」vs「数字经济」）。

---

## Complex / ReAct：多源编排

| 特征标签 | 典型问法 | 常用源 |
|---|---|---|
| `journal_overview_multi` | 学术发展历程、核心作者团队 | SQL overview |
| `collab_multi_source` | 某主题合作最紧密的作者和机构 | SQL + KG |
| `directions_then_papers` | 发文最多的三个研究方向及重要成果 | SQL |
| `teams_and_evolution` | 影响力团队及其方向变化 | SQL |
| `topic_evolution_multi` | 水稻 / AI 等主题的发展演变 | SQL + RAG |

---

## UI 示例问句与预期路由

### 农生 `ZDXBNXB`

| 示例 | 预期 task |
|---|---|
| 近5年发文量前十的作者 | `top_authors`（schema rank author） |
| 近5年研究过水稻发文量前十的作者 | `keyword_authors` |
| 浙江大学相关作者代表性成果 | `institution_authors` |
| 近十年发文趋势和热门关键词 | `yearly_growth` |
| 适合投基因编辑吗 | `submission_fit` |

### 人文 `ZDXBRWB`

| 示例 | 预期 task |
|---|---|
| 近十年发文趋势和热门关键词？ | `yearly_growth` |
| 本刊主要研究方向有哪些？ | `top_directions_with_papers` |
| 浙江大学相关作者有哪些代表性成果？ | `institution_authors` |
| 适合投数字经济吗 | `submission_fit` |

---

## 覆盖弱 / 未覆盖

- **被引排名**：拒答（库无被引字段）。
- **开放综述 / 未命中规则的文献理解**：默认 RAG。
- **schema 低置信**：回退 regex；regex 仍未命中则软兜底或 RAG。
- **主题词抽取**：依赖关键词字段 + 少量内置词；冷门主题可能抽失败。

---

## 相关代码

| 模块 | 路径 |
|---|---|
| Intent Schema | `app/agents/intent_schema.py` |
| Query Understanding | `app/agents/query_understand.py` |
| Capability Planner | `app/agents/capability_planner.py` |
| 复杂度路由 | `app/agents/router_v2.py` |
| 分析子目标 | `app/agents/analysis_planner.py` |
| Simple 执行 | `app/agents/simple_exec.py` |
| ReAct | `app/agents/orchestrator_react.py` |
| SQL 能力 | `app/capabilities/sql_capability.py` |
| 图编排入口 | `app/agents/controller.py` |
