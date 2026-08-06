#!/usr/bin/env python3
"""从各期 ZIP / 已解压目录中提取全部 XML，统一放入 xmls/ 文件夹。"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

# 人文社科版历史上有两种 ZIP 命名
ZIP_GLOBS = (
    "浙江大学学报（人文社会科学版）_*.zip",
    "浙大学报人文社科版_*.zip",
)
DIR_GLOBS = (
    "浙江大学学报（人文社会科学版）_*",
    "浙大学报人文社科版_*",
)


def unique_dest(out_dir: Path, filename: str, seen: set[str]) -> Path | None:
    """按文件名去重；若已存在则跳过。"""
    if filename in seen or (out_dir / filename).exists():
        return None
    seen.add(filename)
    return out_dir / filename


def extract_from_zip(zip_path: Path, out_dir: Path, seen: set[str]) -> int:
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # ZIP 内路径可能因编码乱码，但文件名后缀仍可识别；只写 basename
            name = info.filename.replace("\\", "/")
            if not name.lower().endswith(".xml"):
                continue
            basename = Path(name).name
            dest = unique_dest(out_dir, basename, seen)
            if dest is None:
                continue
            with zf.open(info) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def copy_from_dir(issue_dir: Path, out_dir: Path, seen: set[str]) -> int:
    count = 0
    for xml_path in issue_dir.rglob("*.xml"):
        if not xml_path.is_file():
            continue
        dest = unique_dest(out_dir, xml_path.name, seen)
        if dest is None:
            continue
        shutil.copy2(xml_path, dest)
        count += 1
    return count


def iter_zip_paths(root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in ZIP_GLOBS:
        for zip_path in root.glob(pattern):
            if zip_path.is_file():
                found[zip_path.name] = zip_path
    return sorted(found.values(), key=lambda p: p.name)


def iter_issue_dirs(root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in DIR_GLOBS:
        for issue_dir in root.glob(pattern):
            if issue_dir.is_dir():
                found[issue_dir.name] = issue_dir
    return sorted(found.values(), key=lambda p: p.name)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="提取浙大学报（人文社科）各期 XML 到统一目录")
    parser.add_argument(
        "-o",
        "--output",
        default=str(root / "xmls"),
        help="输出目录（默认：./xmls）",
    )
    parser.add_argument(
        "--skip-zip",
        action="store_true",
        help="不处理 ZIP，只收集已解压目录中的 XML",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="开始前清空输出目录中已有的 .xml",
    )
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clear:
        for old in out_dir.glob("*.xml"):
            old.unlink()

    seen: set[str] = {p.name for p in out_dir.glob("*.xml")}
    total = 0

    if not args.skip_zip:
        for zip_path in iter_zip_paths(root):
            try:
                n = extract_from_zip(zip_path, out_dir, seen)
                print(f"[zip] {zip_path.name}: {n} 个 XML")
                total += n
            except zipfile.BadZipFile as e:
                print(f"[zip][错误] {zip_path.name}: {e}")

    for issue_dir in iter_issue_dirs(root):
        zip_peer = root / f"{issue_dir.name}.zip"
        if zip_peer.exists() and not args.skip_zip:
            continue
        n = copy_from_dir(issue_dir, out_dir, seen)
        print(f"[dir] {issue_dir.name}: {n} 个 XML")
        total += n

    print(f"\n完成：共写入 {total} 个 XML → {out_dir}")
    print(f"目录现有 XML: {len(list(out_dir.glob('*.xml')))} 个")


if __name__ == "__main__":
    main()
