"""step11：只导出独立 annotations_subset.json，不合并原标注、不写划分文件。

    python step11_export_rec8k.py
    python step11_export_rec8k.py --captions anno/subset_captions_local.jsonl --dry-run

标注沿用 图片 → 新 caption → class/attribute/points/type 的格式，额外保存
root_caption、subset_polarity（yes/no）和 complement_caption；不保存 subset_id。
按原图片顺序排列，原始划分仅用于只读校验。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from recsub.config import path
from recsub.console import configure_console
from recsub.export import build_rec8k_subsets
from recsub.io import write_json_atomic


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出独立 REC-8K 子集标注，保留根 caption 来源")
    parser.add_argument("--annotations", type=Path, default=path("annotations"),
                        help="原始 annotations.json")
    parser.add_argument("--splits", type=Path, default=path("splits"),
                        help="原始 train/val/test 划分，仅用于只读校验，不输出新划分")
    parser.add_argument("--subsets", type=Path, default=path("caption_subsets"),
                        help="最终审核子集；成员以这里的 instance_ids 为准")
    parser.add_argument("--captions", type=Path, default=path("captions_online"),
                        help="step9 结果；本地版本请指定 subset_captions_local.jsonl")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--annotations-output", type=Path, default=path("annotations_subset"))
    parser.add_argument("--overwrite", action="store_true", help="允许替换已有标注输出；不允许覆盖任何输入")
    parser.add_argument("--dry-run", action="store_true", help="完整校验和统计，不写文件")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    inputs = {name: getattr(args, name) for name in
              ("annotations", "splits", "subsets", "captions", "instances")}
    for output in [args.annotations_output]:
        if any(output.resolve() == source.resolve()
               or (output.exists() and source.exists() and output.samefile(source))
               for source in inputs.values()):
            raise ValueError(f"输出不能覆盖任何输入：{output}")
        if output.exists() and not args.dry_run and not args.overwrite:
            raise FileExistsError(f"导出文件已存在：{output}；如需更新请加 --overwrite")
    for name, source in inputs.items():
        if not source.is_file():
            hint = "；请先运行 step9 或用 --captions 指定其结果" if name == "captions" else ""
            raise FileNotFoundError(f"找不到 {name}：{source}{hint}")
    annotations, report = build_rec8k_subsets(
        annotations_path=args.annotations, splits_path=args.splits, subsets_path=args.subsets,
        captions_path=args.captions, instances_path=args.instances,
    )
    counts = report["counts"]
    print(f"✅ 可导出 {counts.get('exported_subsets', 0)} 个子集 / "
          f"{counts.get('exported_images', 0)} 张图片；不含原根标注")
    print("   图片划分：" + "，".join(f"{key}={value}" for key, value in report["split_counts"].items()))
    print(f"   正集 {counts.get('positive_subsets', 0)}，负集 {counts.get('negative_subsets', 0)}；"
          f"已关联 {counts.get('linked_subsets', 0)}，无已导出补集 {counts.get('unlinked_subsets', 0)}")
    print(f"   字符串检查 no 但语言审核通过：{counts.get('string_check_no_exported', 0)}")
    for name in ("missing_caption", "caption_failed", "language_not_accepted", "discarded_subset"):
        if counts.get(name):
            print(f"   跳过 {name}：{counts[name]}")
    if counts.get("orphan_caption_records"):
        print(f"   忽略不属于当前最终子集的历史文本：{counts['orphan_caption_records']}")
    if not annotations:
        raise ValueError("没有审核通过且可导出的子集，不写入空标注文件")
    if args.dry_run:
        print("   dry-run：校验通过，未写文件")
        return 0
    write_json_atomic(args.annotations_output, annotations, indent=4)
    print(f"   子集标注 → {args.annotations_output}")
    print("   原始划分未改动，不输出新划分文件")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    try:
        return run(parse_args(argv))
    except KeyboardInterrupt:
        print("导出已中断；原始输入未改动", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 子集导出失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
