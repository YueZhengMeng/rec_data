"""step2：从原始标注派生 (类别, 维度, 取值) 谓词词表。

词表是整条链的上游产物：step3 用它做 few-shot 引导开放提取，step4 用它把候选
词归一化到槽位，step6 的消融需要「仅原词表」这个基线。

默认**只用 train 划分**构建，避免 val/test 的标注词进入提示词。取值至少覆盖
``--min-root-count`` 个根才收录，长尾噪声不进 few-shot。

产物三个：
- ``anno/attribute_vocabulary.json``：程序读取的词表
- ``anno/attribute_vocabulary.md``：人工浏览用
- ``anno/vocabulary_overrides.json``：可选。人工改名/合并/删除/加别名，
  加载时合并，**不用改代码**。格式示例：

      {
        "relabel": {"person/location/in-queue": {"value": "in the queue"}},
        "merge":   {"person/clothing/blue-dress": "person/clothing/dress"},
        "aliases": {"person/accessory/umbrella/verb=holding": ["has an umbrella"]},
        "drop":    ["person/location/some-noise-value"]
      }

    python step2_build_vocabulary.py
    python step2_build_vocabulary.py --split train --min-root-count 3
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.io import load_json_any
from recsub.vocabulary import (
    Vocabulary,
    apply_overrides,
    build_vocabulary,
    load_split_keys,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建标注词表")
    parser.add_argument("--annotations", type=Path, default=path("annotations"),
                        help="原始标注 annotations.json")
    parser.add_argument("--splits", type=Path, default=path("splits"),
                        help="数据划分 splits.json")
    parser.add_argument("--output", type=Path, default=path("vocabulary"),
                        help="词表 JSON 输出路径")
    parser.add_argument("--markdown", type=Path, default=path("vocabulary_md"),
                        help="人工浏览用 Markdown 输出路径")
    parser.add_argument("--overrides", type=Path, default=path("vocabulary_overrides"),
                        help="人工覆盖文件；不存在则跳过")
    parser.add_argument("--split", default=CONFIG["vocabulary"]["split"],
                        choices=("train", "val", "test", "all"),
                        help="只用该划分构建词表，默认 train")
    parser.add_argument("--min-root-count", type=int,
                        default=CONFIG["vocabulary"]["min_root_count"],
                        help="取值至少覆盖这么多根才收录")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if args.min_root_count < 1:
        print("❌ --min-root-count 必须为正整数", file=sys.stderr)
        return 1

    annotations = load_json_any(args.annotations)
    if not isinstance(annotations, Mapping):
        print(f"❌ {args.annotations} 顶层应为对象", file=sys.stderr)
        return 1

    split_keys = None
    if args.split != "all":
        if not args.splits.is_file():
            print(f"❌ 找不到划分文件：{args.splits}", file=sys.stderr)
            return 1
        split_keys = load_split_keys(load_json_any(args.splits), args.split)

    entries, stats = build_vocabulary(
        annotations, split_keys=split_keys, min_root_count=args.min_root_count
    )

    overrides_present = args.overrides.is_file()
    overrides = load_json_any(args.overrides) if overrides_present else None
    entries, applied = apply_overrides(entries, overrides)

    vocabulary = Vocabulary(entries=entries, metadata={
        "source": str(args.annotations),
        "splits": str(args.splits),
        "split": args.split,
        "min_root_count": args.min_root_count,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "categories": dict(Counter(entry.category for entry in entries).most_common()),
        "dimensions": dict(Counter(entry.dimension for entry in entries).most_common()),
        "scene_relative_count": sum(entry.scope == "scene_relative" for entry in entries),
        "stats": stats,
        "overrides_applied": applied,
        "overrides_path": str(args.overrides) if overrides_present else None,
    })
    vocabulary.save(args.output)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(vocabulary.to_markdown(), encoding="utf-8")

    print(f"✅ 词表：{len(vocabulary)} 条 → {args.output}")
    print(f"   人工浏览：{args.markdown}")
    print(f"   划分 {args.split}：根 {stats['raw_roots']} 个（过滤掉 "
          f"{stats['skipped_by_split']} 个），归一化后 {stats['slots_before_min_root_count']} 个槽位，"
          f"因覆盖不足丢弃 {stats['dropped_by_min_root_count']} 个")
    print(f"   场景相对（已排除出 few-shot）：{vocabulary.metadata['scene_relative_count']} 条")
    if overrides_present:
        print(f"   已应用人工覆盖：{applied}")
    else:
        print(f"   未提供 {args.overrides}（可选：人工改名/合并/删除/加别名）")
    print("   按维度：", dict(Counter(entry.dimension for entry in entries).most_common()))
    for category in vocabulary.categories()[:6]:
        per_dimension = {dim: len(items)
                         for dim, items in vocabulary.dimensions_of(category).items()}
        print(f"   {category}: {per_dimension}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
