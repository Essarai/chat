# 期刊知识助手

面向期刊集群的 AI 问答 / 趋势 / 知识图谱。

当前支持两刊物理隔离：
- `ZDXBNXB` 农业与生命科学版 → `data/journal.db` / Chroma `journal_papers` / 自建 Neo4j
- `ZDXBRWB` 人文社会科学版 → `data/journal_rwb.db` / Chroma `journal_papers_rwb` / Neo4j Aura

Web 顶部期刊下拉可切换；API 传 `journal_id`。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入密钥
python3 -m uvicorn app.api.main:app --host 0.0.0.0 --port 8080
```

使用指南：http://127.0.0.1:8080/ ，对话页面：http://127.0.0.1:8080/ask ，
健康检查：http://127.0.0.1:8080/health

会话状态默认持久化到 `data/conversations.db`：LangGraph `SqliteSaver`
保存执行 checkpoint，应用表保存左侧会话目录和结构化 Turn。服务重启后可继续追问；
可通过 `CONVERSATION_DB_PATH` 修改路径。

## LangSmith Trace 与 Eval

在 `.env` 中配置：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-langsmith-api-key
LANGSMITH_PROJECT=journal-qa
```

启用后，每次 `/ask` 和 `/ask/stream` 都会记录为根 trace；同一个
`conversation_id` 会聚合为 LangSmith Thread，LangGraph 节点和 MiniMax 调用显示为子 span。

同步 10 个核心任务数据集并运行生产链路 Eval：

```bash
python3 eval/run_langsmith_eval.py
```

只同步数据集、不执行问答：

```bash
python3 eval/run_langsmith_eval.py --sync-only
```

## Railway 部署

仓库：https://github.com/Essarai/chat

1. [Railway](https://railway.app) → **New Project** → **Deploy from GitHub** → 选择 `Essarai/chat`
2. 构建方式使用仓库内 `Dockerfile`（见 `railway.toml`）
3. **Variables** 按 `.env.example` 填写（不要提交 `.env`）
4. **Settings → Networking → Generate Domain**
5. 验收：`/health` 返回 `ok: true`；首页可打开问答

### 必填环境变量

| 变量 | 说明 |
|------|------|
| `MINIMAX_API_KEY` | MiniMax API Key |
| `MINIMAX_BASE_URL` | 默认 `https://api.minimaxi.com/v1` |
| `MINIMAX_CHAT_MODEL` | 默认 `MiniMax-Text-01` |
| `MINIMAX_EMBED_MODEL` | 默认 `embo-01` |
| `CHROMA_TARGET` | 默认 `cloud`；备选 `http` |
| `CHROMA_CLOUD_API_KEY` / `CHROMA_CLOUD_TENANT` / `CHROMA_CLOUD_DATABASE` | Chroma Cloud（`target=cloud`） |
| `CHROMA_COLLECTION` | 默认 `journal_papers` |
| `CHROMA_HOST` / `CHROMA_PORT` / `CHROMA_TOKEN` | 仅 `CHROMA_TARGET=http` 时需要 |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 远程 Neo4j |
| `SQLITE_PATH` | 默认 `./data/journal.db`（镜像已含库） |
| `ALLOWED_ORIGINS` | 公网部署时配置允许的前端来源，多个来源用逗号分隔 |
| `API_ACCESS_TOKEN` | 可选；设置后除首页、静态资源和 `/health` 外均需 Bearer Token |
| `REQUESTS_PER_MINUTE` | 可选；按客户端 IP 的每分钟请求上限，`0` 表示关闭 |

### 注意

默认向量库为 Chroma Cloud。若改用自建 Chroma（`CHROMA_TARGET=http`），Railway 出口需能访问该主机；Neo4j 同理。

## 远程 MCP 与通用 Agent

本项目把期刊数据查询封装为 14 个只读业务语义工具。通用 Agent 负责理解问题、规划工具调用和组织最终答案，不需要访问 SQLite、Chroma 或 Neo4j 的原始查询接口。

已部署的 Streamable HTTP MCP：

```text
https://journals.up.railway.app/mcp
```

连接时使用请求头 `Authorization: Bearer <MCP_BEARER_TOKEN>`。Token 应保存在 Agent 平台的环境变量或 Secret Manager 中，不要写入提示词或提交到 Git。

通用配置示例：

```json
{
  "mcpServers": {
    "journals": {
      "type": "streamable-http",
      "url": "https://journals.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer ${JOURNAL_MCP_TOKEN}"
      }
    }
  }
}
```

Codex 可以在 `~/.codex/config.toml` 或项目 `.codex/config.toml` 中配置：

```toml
[mcp_servers.journals]
url = "https://journals.up.railway.app/mcp"
bearer_token_env_var = "JOURNAL_MCP_TOKEN"
startup_timeout_sec = 20
tool_timeout_sec = 90
required = false
default_tools_approval_mode = "writes"
```

设置 `JOURNAL_MCP_TOKEN` 并重启 Agent 后，客户端会自动发现工具。Codex 中可使用 `/mcp` 检查连接状态。

- Agent 的业务调用规则与典型编排：[AGENT_GUIDE.md](AGENT_GUIDE.md)
- 工具、部署与测试的完整技术说明：[docs/MCP.md](docs/MCP.md)
- 作者与编辑的公开使用手册：<https://journals.up.railway.app/>
