# Railway 部署步骤（Essarai/chat）

## 0. 推送代码（若尚未推上 GitHub）

在本机 `rag` 目录执行：

```bash
cd "/Users/essarai/workspace/cursor/ rag"
git push -u origin main
```

仓库：https://github.com/Essarai/chat

## 1. 连接 Railway

1. 打开 https://railway.app → **New Project**
2. **Deploy from GitHub repo** → 授权并选择 `Essarai/chat`
3. 确认使用 Dockerfile（仓库已含 `Dockerfile` + `railway.toml`）

## 2. 环境变量

在服务 **Variables** 中添加（值从本地 `.env` 复制，勿提交 `.env`）：

```
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_CHAT_MODEL=MiniMax-Text-01
MINIMAX_EMBED_MODEL=embo-01
CHROMA_TARGET=cloud
CHROMA_COLLECTION=journal_papers
CHROMA_COLLECTION_RWB=journal_papers_rwb
CHROMA_CLOUD_API_KEY=
CHROMA_CLOUD_TENANT=
CHROMA_CLOUD_DATABASE=journals
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_URI_RWB=
NEO4J_USER_RWB=
NEO4J_PASSWORD_RWB=
SQLITE_PATH=./data/journal.db
SQLITE_PATH_RWB=./data/journal_rwb.db
RAG_TOP_K=5
CHAT_MAX_HISTORY=10
```

## 3. 域名与验收

1. **Settings → Networking → Generate Domain**
2. 打开 `https://<your-app>.up.railway.app/health`
3. 期望：`{"ok": true, ...}`，且 `chroma_count` 有值
4. 打开首页 `/`，试预设问答 / 趋势 / 图谱

## 4. 网络注意

- **Chroma Cloud**（默认）：Railway 能出网即可，无需自建 Chroma 端口。
- **Neo4j / 自建 Chroma（`CHROMA_TARGET=http`）**：若出现 `ConnectTimeout`，检查云主机安全组是否对公网开放 Bolt/HTTP 端口，且 Variables 里是公网可达地址。

说明：SQLite 已打进镜像，**发文统计 / 关键词作者** 等 SQL 题不依赖 Chroma；**语义问答 / RAG** 依赖 Chroma Cloud；**图谱** 依赖 Neo4j。
