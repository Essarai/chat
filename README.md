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

打开 http://127.0.0.1:8080/ ，健康检查：http://127.0.0.1:8080/health

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

### 注意

默认向量库为 Chroma Cloud。若改用自建 Chroma（`CHROMA_TARGET=http`），Railway 出口需能访问该主机；Neo4j 同理。
