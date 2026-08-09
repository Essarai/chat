#!/usr/bin/env bash
# 人文社科版本地清洗流水线（不上传任何远程库）
# 用法:
#   ./run_pipeline.sh           # 全量：重解压 + 解析 + 清洗 + 验证
#   ./run_pipeline.sh --reclean # 跳过解压，仅用现有 xmls 重解析/清洗/验证
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-}"

if [[ "$MODE" == "--reclean" ]]; then
  echo "==> 1/4 跳过解压（使用现有 xmls/）"
else
  echo "==> 1/4 提取 XML"
  python3 extract_xmls.py --clear
fi

echo "==> 2/4 解析 metadata CSV"
python3 parse_article_xml.py xmls -o articles_metadata.csv

echo "==> 3/4 KG 清洗 → cleaned/（含办刊/会议剔除）"
python3 clean_for_kg.py

echo "==> 4/4 RAG 清洗 → rag_cleaned/"
python3 clean_for_rag.py

echo "==> 验证清洗结果"
python3 validate_cleaned.py

echo "完成。输出均在本目录，未做 SQLite/Neo4j/Chroma 上传。"
