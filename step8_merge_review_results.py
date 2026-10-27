"""Merge frozen step8 review results and recheck label-sensitive step6 gates.

G1/G5 use the reviewed three-way counts; G4 uses the reviewed yes sets.
G2 is skipped; G3/G6 and the existing gate selection remain unchanged.
After gating, all remaining reviewed unknown members become no: reviewers have
already resolved every member that should be yes. Complements are root minus yes.
No intersections are generated; original review task files remain unchanged.

    python step8_merge_review_results.py --dry-run
    python step8_merge_review_results.py
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path, resolve_dir
from recsub.console import configure_console
from recsub.gates import Counts, GateSettings, evaluate, select_with_redundancy
from recsub.io import iter_jsonl, write_jsonl_atomic
from recsub.review_tasks import (
    BATCH_MANIFEST_NAME, load_and_validate_task, task_paths, validate_task_outputs,
)
from recsub.subsets import stable_id
from step8_review_sheet import effective_partitions

PARTITION_FIELDS = ("instance_ids", "negative_instance_ids", "unknown_instance_ids")
SOURCE_FIELDS = ("yes_ids", "no_ids", "unknown_ids")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    gates = CONFIG["gates"]
    parser = argparse.ArgumentParser(description="整合审核任务，复验 G1/G4/G5，门禁后按 root − yes 生成补集")
    parser.add_argument("--review-dir", type=Path, default=path("review_dir"))
    parser.add_argument("--output", type=Path, default=path("caption_subsets"),
                        help="门禁后正集及对应补集 JSONL（默认 anno/final_subsets.jsonl）")
    parser.add_argument("--decisions", type=Path, default=path("review_subset_decisions"),
                        help="门禁拒绝及末行汇总 JSONL")
    parser.add_argument("--min-support", type=int, default=gates["min_support"])
    parser.add_argument("--min-complement", type=int, default=gates["min_complement"])
    parser.add_argument("--min-balance", type=float, default=gates["min_balance"])
    parser.add_argument("--max-support-jaccard", type=float, default=gates["max_support_jaccard"])
    parser.add_argument("--dry-run", action="store_true", help="完整校验并统计，不写文件")
    return parser.parse_args(argv)


def _read_json(path_: Path) -> dict:
    with path_.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path_} 顶层必须为 JSON 对象")
    return value


def _validate_partition(record: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    """Check edited membership against the frozen parent and recorded label overrides."""
    source_ids: set[int] = set()
    for fields, label in ((SOURCE_FIELDS, "审核清单"), (PARTITION_FIELDS, "最终结果")):
        seen: set[int] = set()
        for field in fields:
            values = item.get(field) if label == "审核清单" else record.get(field)
            if (not isinstance(values, list)
                    or any(type(value) is not int or value < 0 for value in values)
                    or len(values) != len(set(values))):
                raise ValueError(f"{record['review_id']} 的{label} {field} 无效或重复")
            if seen & set(values):
                raise ValueError(f"{record['review_id']} 的{label}三值分区有重叠")
            seen.update(values)
        if label == "审核清单":
            source_ids = seen
        elif seen != source_ids:
            raise ValueError(f"{record['review_id']} 的最终分区未完整覆盖审核清单父集")
    other_ids = item.get("other_instance_ids")
    if (not isinstance(other_ids, list)
            or any(type(value) is not int for value in other_ids)
            or len(other_ids) != len(set(other_ids))
            or set(other_ids) != source_ids - set(item["yes_ids"])):
        raise ValueError(f"{record['review_id']} 的审核清单 other_instance_ids 不一致")
    expected = effective_partitions(item, {"labels": record["review"].get("label_overrides", {})})
    for field, source_field in zip(PARTITION_FIELDS, SOURCE_FIELDS):
        if set(record[field]) != set(expected[source_field]):
            raise ValueError(f"{record['review_id']} 的最终 {field} 与 review_state 标签不一致")


def _refresh_points(record: dict, item: Mapping[str, Any]) -> None:
    """Step8 leaves point arrays unchanged after flips; align them to the edited IDs."""
    point_by_id = {}
    for ids_field, points_field in zip(SOURCE_FIELDS, ("yes_points", "no_points", "unknown_points")):
        ids, points = item[ids_field], item.get(points_field)
        if not isinstance(points, list) or len(ids) != len(points):
            raise ValueError(f"{record['review_id']} 的审核清单 {points_field} 与实例数不一致")
        point_by_id.update(zip(ids, points))
    for ids_field, points_field in zip(PARTITION_FIELDS, ("yes_points", "no_points", "unknown_points")):
        record[ids_field] = sorted(record[ids_field])
        record[points_field] = [point_by_id[value] for value in record[ids_field]]
    record["other_instance_ids"] = sorted(set(point_by_id) - set(record["instance_ids"]))
    record["other_points"] = [point_by_id[value] for value in record["other_instance_ids"]]


def load_review_batch(review_dir: Path) -> list[dict]:
    """Validate frozen tasks, complete states, and final files without changing the inputs."""
    batch = _read_json(review_dir / BATCH_MANIFEST_NAME)
    specs = batch.get("tasks")
    if (batch.get("schema_version") != 1 or not isinstance(specs, list) or not specs
            or type(batch.get("task_count")) is not int or batch["task_count"] != len(specs)):
        raise ValueError("review_batch.json 的 schema_version/task_count/tasks 无效")

    assigned_ids: set[str] = set()
    frozen_ids: set[str] | None = None
    final_records: list[dict] = []
    intervals: list[tuple[int, int]] = []
    seen_tasks: set[str] = set()
    image_indices: set[int] = set()
    for spec in specs:
        if not isinstance(spec, dict):
            raise ValueError("review_batch.json 含无效任务条目")
        task_id, directory = spec.get("task_id"), spec.get("directory")
        if (not isinstance(task_id, str) or not task_id or task_id in seen_tasks
                or directory != task_id or "/" in task_id or "\\" in task_id
                or task_id in {".", ".."} or Path(task_id).is_absolute()):
            raise ValueError(f"审核批次存在无效或重复任务目录：{directory!r}")
        seen_tasks.add(task_id)
        task_dir = review_dir / directory
        if task_dir.resolve().parent != review_dir.resolve():
            raise ValueError(f"任务目录指向批次目录外：{task_dir}")
        items, manifest, fingerprint = load_and_validate_task(task_dir)
        if (manifest["task_id"] != task_id
                or any(manifest[key] != batch.get(key) for key in ("dataset_id", "items_sha256"))
                or any(spec.get(key) != manifest[key] for key in
                       ("start", "end", "item_count", "image_count", "fingerprint"))):
            raise ValueError(f"{task_id} 与 review_batch.json 不一致")
        if frozen_ids is None:
            frozen_ids = {item["review_id"] for item in items}
        start, end = manifest["start"], manifest["end"]
        intervals.append((start, end))
        assigned = [item for item in items if start <= item["image_idx"] < end]
        paths = task_paths(task_dir)
        states = validate_task_outputs(task_dir, manifest, assigned)
        # The browser saves one latest row per ID, so duplicate rows indicate a damaged snapshot.
        if sum(1 for _ in iter_jsonl(paths["state"])) != len(states):
            raise ValueError(f"{task_id} 审核状态包含重复 review_id")
        expected_ids = {item["review_id"] for item in assigned}
        if set(states) != expected_ids:
            raise ValueError(f"{task_id} 审核状态未完整，缺少 {sorted(expected_ids - set(states))[:5]}")
        if any(state.get("status") not in {"keep", "discard"} for state in states.values()):
            raise ValueError(f"{task_id} 仍含 pending/unknown 状态，先完成审核或复核")
        if assigned_ids & expected_ids:
            raise ValueError(f"不同审核任务重复审核清单 ID：{sorted(assigned_ids & expected_ids)[:5]}")
        assigned_ids.update(expected_ids)
        image_indices.update(item["image_idx"] for item in assigned)

        task_finals: dict[str, dict] = {}
        for final in iter_jsonl(paths["final_output"]):
            rid = final.get("review_id")
            if not isinstance(rid, str) or rid not in expected_ids or rid in task_finals:
                raise ValueError(f"{task_id} 最终文件含任务外/重复 review_id：{rid!r}")
            task_finals[rid] = final
        expected_kept = {rid for rid, state in states.items() if state["status"] == "keep"}
        if set(task_finals) != expected_kept:
            raise ValueError(f"{task_id} final_subsets 与状态中的 keep 集合不一致")
        item_by_id = {item["review_id"]: item for item in assigned}
        for rid, record in task_finals.items():
            state, item = states[rid], item_by_id[rid]
            review = record.get("review")
            if (not isinstance(review, dict) or review.get("status") != "keep"
                    or review.get("discarded") is True or review.get("reviewed") is not True
                    or review.get("defaulted", False) != state.get("defaulted", False)
                    or review.get("label_overrides", {}) != state.get("labels", {})):
                raise ValueError(f"{task_id}/{rid} 最终结果与审核状态不一致")
            for field in ("node_id", "image_idx", "image_name", "caption", "category", "predicate", "parent_id"):
                if record.get(field) != item.get(field):
                    raise ValueError(f"{task_id}/{rid} 的 {field} 与冻结清单不一致")
            if (record.get("node_type") != "predicate_subset"
                    or record.get("parent_ids") != [item.get("parent_id")]
                    or record.get("complement_of")
                    or (record.get("predicate") or {}).get("polarity") == "negative"):
                raise ValueError(f"{task_id}/{rid} 不是原始审核正向子集")
            _validate_partition(record, item)
            _refresh_points(record, item)
            record["merge_source"] = {"task_id": task_id, "fingerprint": fingerprint,
                                      "dataset_id": batch["dataset_id"]}
            final_records.append(record)

    if (intervals != sorted(intervals)
            or any(left[1] != right[0] for left, right in zip(intervals, intervals[1:]))
            or intervals[0][0] != batch.get("start") or intervals[-1][1] != batch.get("end")):
        raise ValueError("审核任务区间未排序、不连续或与批次边界不一致")
    if (assigned_ids != frozen_ids or len(assigned_ids) != batch.get("item_count")
            or len(image_indices) != batch.get("image_count")):
        raise ValueError("审核任务未完整覆盖冻结清单或总条数/图片数与批次不一致")
    return final_records


def _thresholds(settings: GateSettings) -> dict[str, Any]:
    return {"min_support": settings.min_support, "min_complement": settings.min_complement,
            "min_balance": settings.min_balance,
            "max_support_jaccard": settings.max_support_jaccard}


def apply_gates(records: Sequence[Mapping[str, Any]], settings: GateSettings
                ) -> tuple[list[dict], list[dict], Counter]:
    """Reuse step6 G1/G5 and G4 ranking on reviewed partitions; skip G2/G3/G6."""
    # 门禁仍按原审核三值分区计算，避免改变已选中的谓词及 G4 排序。
    # 只在门禁后导出正负集合时，将剩余 unknown 按审核约定归入 no。
    count_settings = replace(settings, max_unknown_ratio=1.0)
    selected: list[dict] = []
    rejected: list[dict] = []
    by_root: dict[tuple[str, str], list[tuple[str, Counts, set]]] = defaultdict(list)
    by_predicate: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    seen_ids: set[str] = set()

    def reject(record: Mapping[str, Any], gate: str, counts: dict, detail: str) -> None:
        rejected.append({"record_type": "rejected", "review_id": record["review_id"],
                         "node_id": record.get("node_id"),
                         "task_id": (record.get("merge_source") or {}).get("task_id"),
                         "image_name": record["image_name"], "caption": record["caption"],
                         "predicate_id": record["predicate"]["predicate_id"],
                         "gate": gate, "counts": counts, "thresholds": _thresholds(settings),
                         "detail": detail})

    for record in records:
        rid = record.get("review_id")
        predicate_id = (record.get("predicate") or {}).get("predicate_id")
        root = (record["image_name"], record["caption"])
        if (not isinstance(rid, str) or not rid or rid in seen_ids
                or not isinstance(predicate_id, str) or not predicate_id
                or predicate_id in by_predicate[root]):
            raise ValueError(f"审核结果 ID 缺失或重复：{rid!r}")
        seen_ids.add(rid)
        by_predicate[root][predicate_id] = record
        yes, no, unknown = (set(record[field]) for field in PARTITION_FIELDS)
        counts = Counts(len(yes | no | unknown), len(yes), len(no), len(unknown))
        ok, failed_gate, numbers = evaluate(counts, count_settings)
        if not ok:
            reject(record, failed_gate, numbers, "人工分区未通过 G1/G5 基础门禁（跳过 G2）")
        else:
            by_root[root].append((predicate_id, counts, yes))

    for root in sorted(by_root):
        candidates = by_root[root]
        # All proposals already survived step6's G6; raising the cap to this list length
        # disables that gate while reusing exactly the same G4 ranking and threshold rule.
        selected_tuples, redundancy_rejections = select_with_redundancy(
            candidates, replace(settings, max_predicates_per_root=len(candidates)))
        for rejection in redundancy_rejections:
            record = by_predicate[root][rejection["predicate_id"]]
            reject(record, rejection["gate"], rejection["counts"], rejection["detail"])
        for predicate_id, counts, _ in selected_tuples:
            output = dict(by_predicate[root][predicate_id])
            output["counts"] = counts.to_dict()
            output["gates"] = {
                **(output.get("gates") or {}),
                "support": counts.yes >= settings.min_support,
                "complement": counts.no >= settings.min_complement,
                "unknown_ratio": round(counts.unknown_ratio, 4),
                "unknown_gate_skipped": True,
                "balance": round(counts.balance, 4),
                "balance_ok": counts.balance >= settings.min_balance,
                "max_support_jaccard": settings.max_support_jaccard,
            }
            # 不沿用旧 unknown_ok，避免把 step6 的检查结果误认为本次复验结果。
            output["gates"].pop("unknown_ok", None)
            selected.append(output)
    selected.sort(key=lambda row: (row["image_idx"], row["caption"], row["review_id"]))
    rejected.sort(key=lambda row: (row["image_name"], row["caption"], row["review_id"]))
    return selected, rejected, Counter(row["gate"] for row in rejected)


def finalize_reviewed_partition(positive: Mapping[str, Any], settings: GateSettings) -> dict:
    """门禁后将审核剩余 unknown 归入 no；不改变 yes、不回写冻结审核文件。"""
    output = copy.deepcopy(positive)
    point_by_id: dict[int, list] = {}
    seen: set[int] = set()
    for ids_field, points_field in zip(PARTITION_FIELDS, ("yes_points", "no_points", "unknown_points")):
        ids, points = output.get(ids_field), output.get(points_field)
        if (not isinstance(ids, list) or not isinstance(points, list) or len(ids) != len(points)
                or any(type(value) is not int for value in ids) or len(ids) != len(set(ids))
                or seen & set(ids)):
            raise ValueError(f"{positive.get('node_id')} 的审核成员/坐标分区无效：{ids_field}")
        seen.update(ids)
        point_by_id.update(zip(ids, points))
    old_parent = (output.get("counts") or {}).get("parent", len(seen))
    if old_parent != len(seen):
        raise ValueError(f"{positive.get('node_id')} 的分区未覆盖 parent 计数")
    resolved = set(output.get("post_review_unknown_as_no_instance_ids") or [])
    resolved.update(output["unknown_instance_ids"])
    output["post_review_unknown_as_no_instance_ids"] = sorted(resolved)
    output["negative_instance_ids"] = sorted(seen - set(output["instance_ids"]))
    output["no_points"] = [point_by_id[value] for value in output["negative_instance_ids"]]
    output["unknown_instance_ids"], output["unknown_points"] = [], []
    output["other_instance_ids"] = list(output["negative_instance_ids"])
    output["other_points"] = copy.deepcopy(output["no_points"])
    counts = Counts(len(seen), len(output["instance_ids"]), len(output["negative_instance_ids"]), 0)
    output["counts"] = counts.to_dict()
    output["gates"] = {**(output.get("gates") or {}),
                       "support": counts.yes >= settings.min_support,
                       "complement": counts.no >= settings.min_complement,
                       "unknown_ratio": 0.0, "unknown_gate_skipped": True,
                       "balance": round(counts.balance, 4),
                       "balance_ok": counts.balance >= settings.min_balance}
    output["gates"].pop("unknown_ok", None)
    return output


def build_reviewed_complement(positive: Mapping[str, Any], settings: GateSettings) -> dict:
    """Derive root-minus-yes after review, using step6's stable ID convention."""
    predicate = positive.get("predicate") or {}
    display = predicate.get("display")
    parents = positive.get("parent_ids")
    if (positive.get("complement_of") or predicate.get("polarity") == "negative"
            or not isinstance(display, str) or not display.strip()
            or not isinstance(parents, list) or len(parents) != 1 or not parents[0]):
        raise ValueError(f"{positive.get('node_id')} 不能生成补集：需要正集、属性词和唯一父根")
    positive = finalize_reviewed_partition(positive, settings)
    complement = copy.deepcopy(positive)
    complement_predicate = complement["predicate"]
    complement_predicate.update(
        predicate_id=f"{predicate['predicate_id']}__COMPLEMENT",
        display="not " + display, polarity="negative", source="complement",
    )
    # 与 recsub.subsets.node_from_decision 的空 path 补集 ID 规则相同。
    node_id = stable_id("pred_", parents[0], complement_predicate["predicate_id"], [], positive["node_id"])
    complement.update(node_id=node_id, review_id=node_id, complement_of=positive["node_id"],
                      label_source="derived_reviewed_complement")
    for yes_field, no_field in (("instance_ids", "negative_instance_ids"), ("yes_points", "no_points")):
        complement[yes_field], complement[no_field] = complement[no_field], complement[yes_field]
    # unknown 已在审核后归入 no；补集的另一侧恰好是原 yes。
    point_by_id = dict(zip(positive["instance_ids"], positive["yes_points"]))
    complement["other_instance_ids"] = sorted(point_by_id)
    complement["other_points"] = [point_by_id[i] for i in complement["other_instance_ids"]]
    counts = Counts(positive["counts"]["parent"], len(complement["instance_ids"]),
                    len(complement["negative_instance_ids"]), len(complement["unknown_instance_ids"]))
    complement["counts"] = counts.to_dict()
    complement["gates"].update(
        support=counts.yes >= settings.min_support,
        complement=counts.no >= settings.min_complement,
        unknown_ratio=0.0, balance=round(counts.balance, 4),
        balance_ok=counts.balance >= settings.min_balance,
    )
    # 保留审核来源但明确其为推导结果；点级覆盖需取反，不能冒充独立审核的原标签。
    review = complement["review"]
    review["derived_from"] = positive["node_id"]
    review["label_overrides"] = {
        key: {"yes": "no", "no": "yes"}[label]
        for key, label in review.get("label_overrides", {}).items()
    }
    return complement


def merge_review_results(review_dir: Path, settings: GateSettings
                         ) -> tuple[list[dict], list[dict], dict[str, Any]]:
    records = load_review_batch(review_dir)
    selected, rejected, gate_counts = apply_gates(records, settings)
    per_task = {}
    for record in records:
        task = record["merge_source"]["task_id"]
        per_task.setdefault(task, {"review_kept": 0, "gate_kept": 0, "gate_rejected": 0})["review_kept"] += 1
    for record in selected:
        per_task[record["merge_source"]["task_id"]]["gate_kept"] += 1
    output = []
    node_ids = {record["node_id"] for record in selected}
    resolved_unknown = 0
    for positive in selected:
        resolved_unknown += len(positive["unknown_instance_ids"])
        positive = finalize_reviewed_partition(positive, settings)
        complement = build_reviewed_complement(positive, settings)
        if complement["node_id"] in node_ids:
            raise ValueError(f"补集节点 ID 冲突：{complement['node_id']}")
        node_ids.add(complement["node_id"])
        output.extend((positive, complement))
    for row in per_task.values():
        row["gate_rejected"] = row["review_kept"] - row["gate_kept"]
        row["complement_nodes"] = row["gate_kept"]
        row["output_nodes"] = row["gate_kept"] * 2
    summary = {"record_type": "summary", "review_candidates": len(records),
               "kept_after_gates": len(selected), "rejected": len(rejected),
               "positive_subsets": len(selected), "complement_subsets": len(selected),
               "output_nodes": len(output),
               "complement_policy": "root_minus_yes_after_review",
               "post_review_unknown_as_no_memberships": resolved_unknown,
               "rejected_by_gate": dict(sorted(gate_counts.items())),
               "rechecked_gates": ["G1_support", "G4_subset_redundancy", "G5_balance"],
               "skipped_gates": {"G2_unknown_ratio": "审核只能把 unknown 改为 yes/no，比例不会增加"},
               "unknown_policy": "reviewed_unknown_to_no_after_gates",
               "inherited_gates": ["G3_parent_redundant", "G6_over_cap"],
               "thresholds": _thresholds(settings), "tasks": dict(sorted(per_task.items()))}
    return output, rejected, summary


def run(args: argparse.Namespace) -> int:
    review_dir = resolve_dir(args.review_dir).resolve()
    output_path, decisions_path = resolve_dir(args.output).resolve(), resolve_dir(args.decisions).resolve()
    if (output_path == decisions_path or review_dir in output_path.parents
            or review_dir in decisions_path.parents or review_dir in (output_path, decisions_path)):
        raise ValueError("输出和决策日志必须为不同路径，且不能覆盖审核目录中的输入文件")
    settings = GateSettings(
        min_support=args.min_support, min_complement=args.min_complement,
        max_unknown_ratio=1.0, min_balance=args.min_balance,
        max_support_jaccard=args.max_support_jaccard,
        max_predicates_per_root=CONFIG["gates"]["max_predicates_per_root"],
        max_depth=1, emit_complement=False,
    )
    selected, rejected, summary = merge_review_results(review_dir, settings)
    if not args.dry_run:
        # All input validation and gate calculation precede any output writes.
        write_jsonl_atomic(output_path, selected)
        write_jsonl_atomic(decisions_path, [*rejected, summary])
    print(f"✅ {'只读预检' if args.dry_run else '审核结果汇总'}：候选 {summary['review_candidates']}，"
          f"门禁后正集 {summary['positive_subsets']}，拒绝 {len(rejected)}")
    print(f"   同步生成补集 {summary['complement_subsets']}；输出共 {summary['output_nodes']} 个节点")
    print(f"   按门禁拒绝：{summary['rejected_by_gate']}")
    print("   复验 G1/G4/G5；跳过 G2；沿用 G3/G6；门禁后将审核剩余 unknown 归入 no，补集=root−yes")
    print(f"   已归入 no 的 unknown 成员归属：{summary['post_review_unknown_as_no_memberships']}")
    if not args.dry_run:
        print(f"   子集 → {output_path}\n   决策日志 → {decisions_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        return run(args)
    except Exception as error:
        print(f"❌ 审核结果汇总失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
