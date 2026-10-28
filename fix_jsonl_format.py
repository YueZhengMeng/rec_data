"""把被写成「缩进 JSON」的 .jsonl 文件重新压成一行一条记录的 JSONL。

有些 ``.jsonl`` 文件的内容是每一条记录 ``indent=2`` 格式化后首尾相接的 JSON 流，
``iter_jsonl`` 按行解析会失败，下游步骤读不动。

本脚本流式解析源文件（兼容 JSONL、顶层数组、首尾相接 JSON 流三种写法），
逐条 ``json.dumps`` 后写一行，默认原地原子替换；已经是一行一条的文件默认跳过。

    python fix_jsonl_format.py                    # 递归修复当前目录下所有 .jsonl
    python fix_jsonl_format.py --no-recursive     # 只处理当前目录一层
    python fix_jsonl_format.py anno path/to/data  # 指定多个目录/文件
    python fix_jsonl_format.py --dry-run          # 只统计条数，不写文件
    python fix_jsonl_format.py --output-dir anno_fixed   # 另存，不改原文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Sequence

from recsub.config import resolve_dir
from recsub.console import configure_console
from recsub.io import iter_json_records

_JSONL_KWARGS = {"ensure_ascii": False, "separators": (",", ":")}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把缩进 JSON 形式的 .jsonl 重新保存为一行一条记录")
    parser.add_argument("paths", nargs="*", type=Path,
                        help="待处理的文件或目录；默认当前目录")
    parser.add_argument("--pattern", default="*.jsonl",
                        help="目录下的文件名匹配模式（默认 *.jsonl）")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="递归搜索子目录（默认开启；--no-recursive 只处理一层）")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="统一输出目录；默认原地原子替换")
    parser.add_argument("--force", action="store_true",
                        help="已经是一行一条的文件也重写")
    parser.add_argument("--dry-run", action="store_true",
                        help="只解析并统计，不写任何文件")
    args = parser.parse_args(argv)
    if not args.paths:
        args.paths = [Path.cwd()]
    return args


def is_line_jsonl(path: Path) -> bool:
    """每个非空行都能独立解析时，文件已经是标准 JSONL。"""
    has_line = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            has_line = True
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                return False
    return has_line


def iter_records(path: Path) -> Iterable[dict]:
    """逐条产出记录，顺带把「顶层不是对象」的文件拦在写盘之前。"""
    records = iter_json_records(path)
    try:
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise ValueError(f"{path} 第 {index} 条记录不是 JSON 对象")
            yield record
    finally:
        # 显式关闭源文件句柄，原地替换在 Windows 上才不会失败。
        records.close()


def convert_file(source: Path, destination: Path) -> int:
    """流式转换成一行一条，写临时文件并原子替换目标，返回记录数。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    count = 0
    records = iter_records(source)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, **_JSONL_KWARGS) + "\n")
                count += 1
            if count == 0:
                raise ValueError(f"{source} 没有解析出任何记录")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        # Windows 上目标文件仍被打开时无法 replace，这里显式关闭源句柄。
        records.close()
    os.replace(temporary, destination)
    return count


def count_records(source: Path) -> int:
    """``--dry-run`` 用：只解析，不落盘。"""
    records = iter_records(source)
    try:
        return sum(1 for _ in records)
    finally:
        records.close()


def collect_files(paths: Iterable[Path], pattern: str, recursive: bool) -> list[Path]:
    """展开目录参数并去重；保持命令行给出的顺序。"""
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = resolve_dir(raw)
        if path.is_dir():
            matches = path.rglob(pattern) if recursive else path.glob(pattern)
            # 递归时跳过 .git、.idea 等隐藏目录，避免把仓库元数据当成数据文件。
            candidates = sorted(
                match for match in matches
                if match.is_file()
                and not any(part.startswith(".") for part in match.relative_to(path).parts)
            )
        elif path.is_file():
            candidates = [path]
        else:
            raise FileNotFoundError(f"找不到路径：{path}")
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)
    return files


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return f"{size:.1f}GB"


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    files = collect_files(args.paths, args.pattern, args.recursive)
    if not files:
        print(f"⚠️ 没有匹配到 {args.pattern} 文件")
        return 1

    output_dir = resolve_dir(args.output_dir) if args.output_dir else None
    destinations: dict[Path, Path] = {}
    for source in files:
        destination = source if output_dir is None else output_dir / source.name
        if destination in destinations:
            print(f"⚠️ 输出文件名冲突：{source} 与 {destinations[destination]} "
                  f"都会写到 {destination}", file=sys.stderr)
            return 1
        destinations[source] = destination

    converted = skipped = failed = 0
    total_records = 0
    for source in files:
        destination = destinations[source]
        try:
            if not args.force and is_line_jsonl(source):
                skipped += 1
                print(f"· 跳过（已是标准 JSONL）：{source}")
                continue
            if args.dry_run:
                count = count_records(source)
                state = "预演"
            else:
                count = convert_file(source, destination)
                state = "已原地重写" if destination == source else f"已写出 {destination}"
            converted += 1
            total_records += count
            print(f"✔ {state}：{source}（{human_size(source.stat().st_size)}，"
                  f"{count} 条记录）")
        except (ValueError, OSError) as error:
            failed += 1
            print(f"⚠️ 失败：{source}：{error}", file=sys.stderr)

    print(f"\n完成：转换 {converted} 个文件，跳过 {skipped} 个，失败 {failed} 个；"
          f"共写出 {total_records} 条记录")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
