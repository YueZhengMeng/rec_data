"""把图片 → caption 嵌套的候选 JSON 转成一行一条记录的 JSONL。

输入形如 ``{图片: {caption: {category, parent_predicate_id, candidates, dropped}}}``，
输出把图片名和 caption 提升为记录字段：

    {"image_name": "...", "caption": "...", "category": "...", ...}

这样 step4 可以流式写出、step5 可以流式读取，不必先把整个文件解析成嵌套字典。
读取端 :func:`recsub.judgment.load_caption_predicates` 也能直接读嵌套 ``.json``。

    python convert_predicate_candidates.py path/to/a.json
    python convert_predicate_candidates.py a.json b.json --overwrite
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from recsub.config import resolve_dir
from recsub.console import configure_console
from recsub.io import iter_json_object_items, write_jsonl_atomic


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="嵌套候选 JSON → 一行一条 JSONL")
    parser.add_argument("paths", nargs="+", type=Path, help="待转换的 .json 文件")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="输出目录；默认与源文件同目录")
    parser.add_argument("--overwrite", action="store_true",
                        help="目标 .jsonl 已存在时覆盖")
    return parser.parse_args(argv)


def iter_candidate_records(source: Path) -> Iterator[dict]:
    """逐条产出扁平记录，校验嵌套结构没有缺层或类型漂移。"""
    for image_name, captions in iter_json_object_items(source):
        if not isinstance(captions, Mapping):
            raise ValueError(f"{source}: 图片 {image_name!r} 的 caption 数据必须是对象")
        for caption, entry in captions.items():
            if not isinstance(entry, Mapping):
                raise ValueError(
                    f"{source}: {image_name!r} / {caption!r} 的记录必须是对象")
            if "image_name" in entry or "caption" in entry:
                raise ValueError(
                    f"{source}: {image_name!r} / {caption!r} 的记录已自带根字段")
            yield {"image_name": image_name, "caption": caption, **entry}


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    output_dir = resolve_dir(args.output_dir) if args.output_dir else None
    failed = 0
    total = 0
    for raw in args.paths:
        source = resolve_dir(raw)
        destination = (output_dir / source.name if output_dir else source).with_suffix(".jsonl")
        if destination.resolve() == source.resolve():
            print(f"⚠️ 跳过：{source} 已经是 .jsonl", file=sys.stderr)
            failed += 1
            continue
        if destination.exists() and not args.overwrite:
            print(f"⚠️ 跳过：{destination} 已存在（需要时加 --overwrite）", file=sys.stderr)
            failed += 1
            continue
        try:
            count = write_jsonl_atomic(destination, iter_candidate_records(source))
        except (ValueError, OSError) as error:
            print(f"⚠️ 失败：{source}：{error}", file=sys.stderr)
            failed += 1
            continue
        total += count
        print(f"✅ {source} → {destination}（{count} 条记录）")
    print(f"\n完成：共写出 {total} 条记录；失败 {failed} 个文件")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
