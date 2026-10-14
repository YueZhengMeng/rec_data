"""step1：把原始标注摊平成实例表（JSONL）。

输出 ``anno/instance_data.jsonl``，一条一个「图片 × caption × 点」：

    image_name, caption, category, attributes, point, type,
    instance_id, image_idx, caption_idx, point_idx

``instance_id`` 按「图片 → caption → 点」的顺序递增编号；下游的续跑判断、与只带
``instance_id`` 的提取记录的归并配对都依赖这个顺序，所以顺序本身是产物契约的一部分。

JSONL 便于流式读取，且下游按实例追加写盘时不必回写整个文件。

    python step1_data_preprocess.py
    python step1_data_preprocess.py --limit-images 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator, Sequence

from recsub.config import path
from recsub.console import configure_console
from recsub.io import write_jsonl_atomic


def iter_instances(annotations: dict) -> Iterator[dict]:
    """按「图片 → caption → 点」的顺序摊平并递增编号。"""
    instance_id = 0
    for image_idx, (image_name, image_annotations) in enumerate(annotations.items()):
        for caption_idx, (caption, caption_annotation) in enumerate(image_annotations.items()):
            for point_idx, point in enumerate(caption_annotation["points"]):
                yield {
                    "image_name": image_name,
                    "caption": caption,
                    "category": caption_annotation["class"],
                    "attributes": caption_annotation["attribute"],
                    "point": point,
                    "type": caption_annotation["type"],
                    "instance_id": instance_id,
                    "image_idx": image_idx,
                    "caption_idx": caption_idx,
                    "point_idx": point_idx,
                }
                instance_id += 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="摊平原始标注为实例表 JSONL")
    parser.add_argument("--annotations", type=Path, default=path("annotations"),
                        help="原始标注 annotations.json")
    parser.add_argument("--output", type=Path, default=path("instance_data"),
                        help="实例表输出路径（.jsonl）")
    parser.add_argument("--limit-images", type=int, default=0,
                        help="只处理前 N 张图（0 表示全部），用于小样本调试")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if not args.annotations.is_file():
        print(f"❌ 找不到原始标注：{args.annotations}", file=sys.stderr)
        return 1

    with args.annotations.open("r", encoding="utf-8") as handle:
        annotations = json.load(handle)
    if not isinstance(annotations, dict):
        print("❌ 原始标注顶层应为「图片名 → 标注」对象", file=sys.stderr)
        return 1

    selected = annotations
    if args.limit_images > 0:
        image_names = list(annotations)[:args.limit_images]
        selected = {name: annotations[name] for name in image_names}

    total = write_jsonl_atomic(args.output, iter_instances(selected))
    caption_roots = sum(len(records) for records in selected.values())
    print(f"✅ 实例表：{total} 个实例，{caption_roots} 个 image-caption 根，"
          f"{len(selected)} 张图 → {args.output}")
    if args.limit_images > 0:
        print(f"⚠️ 本次只处理前 {args.limit_images} 张图（--limit-images）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
