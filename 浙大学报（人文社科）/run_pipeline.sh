#!/usr/bin/env bash
# 人文社科版本地清洗流水线（不上传任何远程库）
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/4 提取 XML"
python3 extract_xmls.py --clear

echo "==> 2/4 解析 metadata CSV"
python3 parse_article_xml.py xmls -o articles_metadata.csv

echo "==> 3/4 KG 清洗 → cleaned/"
python3 clean_for_kg.py

echo "==> 4/4 RAG 清洗 → rag_cleaned/"
python3 clean_for_rag.py

echo "完成。输出均在本目录，未做 SQLite/Neo4j/Chroma 上传。"
