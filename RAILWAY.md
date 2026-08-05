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
CHROMA_HOST=
CHROMA_PORT=8000
CHROMA_TOKEN=
CHROMA_COLLECTION=journal_papers
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=
SQLITE_PATH=./data/journal.db
RAG_TOP_K=5
CHAT_MAX_HISTORY=10
```

## 3. 域名与验收

1. **Settings → Networking → Generate Domain**
2. 打开 `https://<your-app>.up.railway.app/health`
3. 期望：`{"ok": true, ...}`，且 `chroma_count` 有值
4. 打开首页 `/`，试预设问答 / 趋势 / 图谱

## 4. 网络注意（ConnectTimeout 必看）

`httpcore.ConnectTimeout` / `Connection timed out` 表示 **Railway 访问不到** 你的 Chroma（HTTP）或 Neo4j（Bolt）。

常见原因：云主机安全组 / 防火墙只放行了你的办公网 IP，未对公网开放。

请在 Chroma/Neo4j 所在机器上：

1. **安全组入站**放行：
   - Chroma：`TCP 8000`（或你实际端口）来源 `0.0.0.0/0`（或至少不限制为单 IP）
   - Neo4j：`TCP 7687`
2. 本机确认监听 `0.0.0.0` 而非仅 `127.0.0.1`
3. 用手机流量或另一台海外机器测试：
   ```bash
   curl -m 10 http://YOUR_IP:8000/api/v2/heartbeat
   ```
4. Railway Variables 里的 `CHROMA_HOST` / `NEO4J_URI` 必须是公网可达地址

说明：SQLite 已打进镜像，**发文统计 / 关键词作者** 等 SQL 题不依赖 Chroma；**语义问答 / RAG** 和 **图谱** 必须打通上述网络。
