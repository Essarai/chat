from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.orchestrator import ChatOrchestrator


BANNER = """
================================================
  AI 期刊知识助手  |  《浙江大学学报（农生版）》
  命令: exit/quit 退出  |  clear 清空会话
================================================
示例:
  - 水稻抗病相关有哪些研究？
  - 近十年发文趋势和热门关键词？
  - 朱军老师的合作者有哪些？
"""


def run_repl(once: str | None = None) -> int:
    settings = get_settings()
    if not settings.minimax_api_key:
        print("错误: 未设置 MINIMAX_API_KEY（请配置 .env）", file=sys.stderr)
        return 1

    bot = ChatOrchestrator(settings)
    print(BANNER)
    try:
        from app.services.chroma_store import ChromaStore

        count = ChromaStore(settings).count()
        where = (
            f"Cloud/{settings.chroma_cloud_database}"
            if settings.chroma_uses_cloud
            else f"{settings.chroma_host}:{settings.chroma_port}"
        )
        print(f"已连接 Chroma({where})/{settings.chroma_collection}  docs≈{count}")
    except Exception as e:
        print(f"警告: Chroma 暂不可用: {e}")

    def handle(question: str) -> None:
        q = question.strip()
        if not q:
            return
        if q.lower() in {"exit", "quit", "q"}:
            raise SystemExit(0)
        if q.lower() in {"clear", "reset"}:
            bot.reset()
            print("会话已清空。")
            return
        print("思考中...")
        try:
            result = bot.ask(q)
        except Exception as e:
            print(f"[错误] {e}")
            return
        print(
            f"\n[意图: {result.intent} | intents={result.intents} | "
            f"{result.route_reason}]\n"
        )
        print(result.answer)
        # Only show paper citations for RAG answers
        if result.citations and "rag" in (result.intents or []):
            print("\n--- 引用 ---")
            for i, c in enumerate(result.citations, 1):
                print(
                    f"{i}. {c.get('title')} ({c.get('year')})\n"
                    f"   DOI: {c.get('doi')}  dist={c.get('distance')}\n"
                    f"   链接: {c.get('url')}"
                )
        print()

    if once:
        handle(once)
        return 0

    while True:
        try:
            question = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        try:
            handle(question)
        except SystemExit:
            print("再见。")
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 期刊知识助手终端 Bot")
    parser.add_argument("-q", "--question", help="单次提问后退出")
    args = parser.parse_args(argv)
    return run_repl(once=args.question)


if __name__ == "__main__":
    raise SystemExit(main())
