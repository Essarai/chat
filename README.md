# 青禾 · 期刊知识助手

《浙江大学学报（农业与生命科学版）》AI 问答 / 趋势 / 知识图谱。

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
| `CHROMA_HOST` / `CHROMA_PORT` / `CHROMA_TOKEN` / `CHROMA_COLLECTION` | 远程 Chroma |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 远程 Neo4j |
| `SQLITE_PATH` | 默认 `./data/journal.db`（镜像已含库） |

### 注意

Railway 出口需能访问你的 Chroma / Neo4j 主机。若安全组仅放行本机 IP，请放行 Railway 或改用公网可达地址。
