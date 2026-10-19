"""step6：门禁筛选 + 三值分区子集。

1. 对每个 (image, caption, predicate) 聚合 yes/no/unknown；
2. 用 G1–G3/G5 过滤（支持、补集、unknown、平衡、父冗余）；
3. 在同一根内按区分力排序，用支持集 Jaccard 和每根最多 3 条施加 G4/G6；
4. 通过的谓词输出显式三值分区，并可输出一个 ``not predicate`` 兄弟节点；
5. ``--max-depth 2`` 时只对已通过的一层谓词求交，交集仍须过门禁。

门禁默认值取自 ``recsub/config.py`` 的 ``gates`` 段，每条拒绝理由和实际数值写入
``subset_decisions_log.jsonl``。这份日志不是垃圾：它告诉你某张图/某个 caption 为什么
不可划分，也直接支撑人工审核的候选清单。

可视化与人工审核分别由 step7、step8 完成；step6 的输出 ``review.status=pending``。

    python step6_build_subsets.py --labels anno/predicate_labels.jsonl
    python step6_build_subsets.py --labels anno/predicate_labels.jsonl --max-depth 2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.gates import GateSettings
from recsub.io import iter_jsonl, write_jsonl_atomic
from recsub.normalize import flatten_candidates
from recsub.subsets import build_tree, stable_id


def load_candidates(path_: Path) -> tuple[dict[tuple[str, str], list[dict]], dict[tuple[str, str], str | None]]:
    with path_.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    result = {}
    parent_ids = {}
    for image_name, captions in payload.items():
        for caption, entry in captions.items():
            key = (str(image_name), str(caption))
            result[key] = flatten_candidates(entry)
            parent_ids[key] = entry.get("parent_predicate_id")
    return result, parent_ids


def load_label_records(path_: Path) -> dict[tuple[str, str], dict[int, dict]]:
    """按根收集标签；失败记录不进入三值统计。"""
    grouped: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    for record in iter_jsonl(path_, strict=False):
        if record.get("judgment_failed"):
            continue
        key = (str(record["image_name"]), str(record["caption"]))
        grouped[key][int(record["instance_id"])] = dict(record)
    return grouped


def load_instances(path_: Path, selected_roots: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for instance in iter_jsonl(path_):
        key = (str(instance["image_name"]), str(instance["caption"]))
        if key in selected_roots:
            grouped[key].append(dict(instance))
    return grouped


def merge_records(instances: list[dict], labels: Mapping[int, dict]) -> list[dict]:
    """保留父根的完整点集，并把判断标签补到每个实例；缺失进入 unknown。"""
    result = []
    for instance in instances:
        label = labels.get(int(instance["instance_id"]))
        if label is None:
            result.append({**instance, "labels": {}, "judgment_missing": True})
        else:
            result.append({**instance, "labels": label.get("labels") or {},
                           "judgment_missing": False})
    return result


def root_node(records: list[dict], root_id: str) -> dict[str, Any]:
    first = records[0]
    return {
        "node_id": root_id,
        "node_type": "caption_root",
        "parent_ids": [],
        "image_name": first["image_name"],
        "category": first["category"],
        "caption": first["caption"],
        "support_count": len(records),
        "instance_ids": [int(record["instance_id"]) for record in records],
        "points": [record["point"] for record in records],
        "review": {"status": "source"},
    }


def parent_redundant_ids(predicates: Sequence[Mapping[str, Any]], parent: Mapping[str, Any]) -> set[str]:
    """复验 step4 的父冗余硬过滤。

    step4 正常产物已经没有这些条目；如果用户手工编辑候选文件把它们加回来，
    这里仍然挡住。这里按 ``parent_predicate_id`` 比较，避免重新猜文本语义。
    """
    parent_id = parent.get("parent_predicate_id")
    return {str(predicate["predicate_id"]) for predicate in predicates
            if str(predicate.get("predicate_id")) == str(parent_id)}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="门禁筛选三值分区子集")
    parser.add_argument("--labels", type=Path, default=path("predicate_labels_online"),
                        help="step5 三值判断 JSONL")
    parser.add_argument("--candidates", type=Path, default=path("predicate_candidates_online"),
                        help="step4 归一化候选 JSON")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--output", type=Path, default=path("subsets"),
                        help="子集节点 JSONL")
    parser.add_argument("--decisions", type=Path, default=path("subset_decisions"),
                        help="门禁拒绝日志 JSONL")
    parser.add_argument("--min-support", type=int, default=CONFIG["gates"]["min_support"])
    parser.add_argument("--min-complement", type=int, default=CONFIG["gates"]["min_complement"])
    parser.add_argument("--max-unknown-ratio", type=float, default=CONFIG["gates"]["max_unknown_ratio"])
    parser.add_argument("--min-balance", type=float, default=CONFIG["gates"]["min_balance"])
    parser.add_argument("--max-support-jaccard", type=float,
                        default=CONFIG["gates"]["max_support_jaccard"])
    parser.add_argument("--max-predicates-per-root", type=int,
                        default=CONFIG["gates"]["max_predicates_per_root"])
    parser.add_argument("--max-depth", type=int, default=CONFIG["gates"]["max_depth"])
    parser.add_argument("--emit-complement", action=argparse.BooleanOptionalAction,
                        default=CONFIG["gates"]["emit_complement"],
                        help="为通过门禁的谓词生成否定侧兄弟子集")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    candidates, parent_ids = load_candidates(args.candidates)
    label_roots = load_label_records(args.labels)
    selected_roots = set(candidates) & set(label_roots)
    parents = load_instances(args.instances, selected_roots)
    settings = GateSettings(
        min_support=args.min_support,
        min_complement=args.min_complement,
        max_unknown_ratio=args.max_unknown_ratio,
        min_balance=args.min_balance,
        max_support_jaccard=args.max_support_jaccard,
        max_predicates_per_root=args.max_predicates_per_root,
        max_depth=args.max_depth,
        emit_complement=args.emit_complement,
    )

    nodes: list[dict] = []
    rejected: list[dict] = []
    stats = defaultdict(int)
    for key in sorted(selected_roots):
        instances = parents.get(key, [])
        if not instances:
            rejected.append({"image_name": key[0], "caption": key[1],
                             "gate": "missing_parent_instances", "counts": {}})
            continue
        records = merge_records(instances, label_roots[key])
        predicates = candidates[key]
        first = records[0]
        root_id = stable_id("caption_", key[0], key[1], first["category"])
        nodes.append(root_node(records, root_id))

        selected_nodes, root_rejected = build_tree(
            records, predicates, root_id, settings,
            parent_redundant_ids=parent_redundant_ids(predicates, {
                "parent_predicate_id": parent_ids.get(key),
            }),
        )
        for node in selected_nodes:
            node["image_name"] = key[0]
            node["category"] = first["category"]
            node["caption"] = key[1]
            node["review"] = {"status": "pending"}
            nodes.append(node)
        for item in root_rejected:
            rejected.append({"image_name": key[0], "caption": key[1], **item})
        stats["roots"] += 1
        stats["source_instances"] += len(records)
        stats["selected_nodes"] += len(selected_nodes)
        stats["rejected_proposals"] += len(root_rejected)

    def node_records():
        yield from nodes

    def rejected_records():
        for item in rejected:
            yield item

    write_jsonl_atomic(args.output, node_records())
    write_jsonl_atomic(args.decisions, rejected_records())

    print(f"✅ 子集构建：{stats['roots']} 个根，{stats['source_instances']} 个实例")
    print(f"   通过节点（含 complement）：{stats['selected_nodes']}；"
          f"拒绝提案：{stats['rejected_proposals']}")
    print(f"   门禁：support≥{args.min_support} / complement≥{args.min_complement} / "
          f"unknown≤{args.max_unknown_ratio:.0%} / balance≥{args.min_balance:.0%} / "
          f"每根最多 {args.max_predicates_per_root} 个 / depth≤{args.max_depth}")
    print(f"   节点 → {args.output}")
    print(f"   拒绝日志 → {args.decisions}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    for name in ("labels", "candidates", "instances"):
        if not getattr(args, name).is_file():
            print(f"❌ 找不到 {name}：{getattr(args, name)}", file=sys.stderr)
            return 1
    try:
        return run(args)
    except Exception as error:
        print(f"❌ 子集构建失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
