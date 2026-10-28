"""Step13：最终只读审计，并可选删除未通过等价门禁的互补 pair。

默认 dry-run，只输出报告；``--apply`` 仅修改 ``annotations_subset.json``，
原始 ``annotations.json`` 始终只读。

    python step13_final_audit.py
    python step13_final_audit.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

from recsub.config import path
from recsub.console import configure_console
from recsub.extract import file_digest
from recsub.gate_audit import audit_step8_gates
from recsub.io import write_json_atomic
from recsub.subset_audit import (audit_pair_overlaps, duplicate_report, read_json,
                                 root_text_conflicts, same_text_different_points)


def build_export_gate_records(subsets: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    """把导出的正/负 pair 还原成 step8 门禁可审计的记录。"""
    records = []
    for image, captions in subsets.items():
        for yes_caption, yes_record in captions.items():
            if yes_record.get("subset_polarity") != "yes":
                continue
            no_caption = yes_record.get("complement_caption")
            no_record = captions.get(no_caption)
            root = yes_record.get("root_caption")
            if not isinstance(no_caption, str) or not isinstance(no_record, dict) or not isinstance(root, str):
                continue
            root_points, seen = [], set()
            for point in raw.get(image, {}).get(root, {}).get("points", []):
                key = tuple(point)
                if key not in seen:
                    seen.add(key)
                    root_points.append(key)
            point_ids = {point: index for index, point in enumerate(root_points)}
            records.append({"review_id": f"export:{image}:{yes_caption}",
                            "node_id": f"export:{image}:{yes_caption}", "image_name": image,
                            "caption": root,
                            "predicate": {"predicate_id": yes_caption, "display": yes_caption},
                            "instance_ids": sorted(point_ids[tuple(point)] for point in yes_record.get("points", [])),
                            "negative_instance_ids": sorted(
                                point_ids[tuple(point)] for point in no_record.get("points", [])),
                            "unknown_instance_ids": []})
    return records


def delete_gate_failed_pairs(subsets: dict[str, Any], rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = {(row.get("image_name"), row.get("caption"), row.get("predicate_id")) for row in rejected}
    deleted, seen_pairs = [], set()
    for image, root, predicate_id in sorted(targets):
        candidates = [(caption, record) for caption, record in subsets.get(image, {}).items()
                      if record.get("root_caption") == root and record.get("subset_polarity") == "yes"
                      and (caption == predicate_id or record.get("attribute") == predicate_id)]
        if len(candidates) != 1:
            raise ValueError(f"无法安全映射门禁失败正集：{image}/{root}/{predicate_id}")
        yes_caption, yes_record = candidates[0]
        no_caption = yes_record.get("complement_caption")
        no_record = subsets[image].get(no_caption)
        if (not isinstance(no_caption, str) or not isinstance(no_record, dict)
                or no_record.get("subset_polarity") != "no"
                or no_record.get("root_caption") != root
                or no_record.get("complement_caption") != yes_caption):
            raise ValueError(f"互补记录不完整，拒绝部分删除：{image}/{yes_caption}")
        pair = (image, yes_caption, no_caption)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        deleted.append({"image_name": image, "root_caption": root, "predicate_id": predicate_id,
                        "yes_caption": yes_caption, "no_caption": no_caption})
    for row in deleted:
        subsets[row["image_name"]].pop(row["yes_caption"])
        subsets[row["image_name"]].pop(row["no_caption"])
    return deleted


def audit_report(raw: dict[str, Any], subsets: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return {"operation": "step13_final_audit",
            "inputs": {"annotations": str(args.annotations), "subsets": str(args.subsets)},
            "raw_annotations": duplicate_report(raw), "subset_annotations": duplicate_report(subsets),
            "pair_audit": audit_pair_overlaps(subsets, raw),
            "step8_gate_audit": audit_step8_gates(build_export_gate_records(subsets, raw)),
            "step8_gate_audit_policy": {
                "source": "step8_merge_review_results.apply_gates",
                "checked": ["G1_support", "G4_subset_redundancy", "G5_balance"],
                "skipped_or_inherited": ["G2_unknown_ratio", "G3_parent_redundant", "G6_over_cap"],
                "counts_are_based_on": "unique coordinate sets in annotations_subset.json"},
            "same_text_different_point_set_conflicts": same_text_different_points(subsets, raw),
            "caption_text_conflicts": root_text_conflicts(subsets, raw),
            "writes_raw_annotations": False}


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw, subsets = read_json(args.annotations), read_json(args.subsets)
    before_raw, before_subset = file_digest(args.annotations), file_digest(args.subsets)
    before = audit_report(raw, subsets, args)
    rejected = before["step8_gate_audit"]["rejected"]
    candidate = json.loads(json.dumps(subsets, ensure_ascii=False))
    planned_deletions = delete_gate_failed_pairs(candidate, rejected)
    deleted = []
    applied = bool(args.apply and planned_deletions)
    if applied:
        if args.annotations.resolve() == args.subsets.resolve():
            raise ValueError("原始 annotations 与 subset 输入路径相同")
        if args.backup.exists() and not args.overwrite_backup:
            raise FileExistsError(f"备份已存在：{args.backup}")
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.subsets, args.backup)
        write_json_atomic(args.subsets, candidate)
        if file_digest(args.annotations) != before_raw:
            raise RuntimeError("Step13 apply 修改了原始 annotations.json")
        deleted = planned_deletions
        report = audit_report(read_json(args.annotations), read_json(args.subsets), args)
    else:
        report = before
    report["step13_apply"] = {
        "applied": applied,
        "no_op": bool(args.apply and not planned_deletions),
        "planned_delete_pair_count": len(planned_deletions),
        "planned_delete_pairs": [
            {key: row[key] for key in ("image_name", "root_caption", "predicate_id", "yes_caption", "no_caption")}
            for row in planned_deletions],
        "deleted_pair_count": len(deleted), "deleted_pairs": deleted,
        "before_raw_sha256": before_raw, "before_subset_sha256": before_subset,
        "after_raw_sha256": file_digest(args.annotations), "after_subset_sha256": file_digest(args.subsets),
        "backup": str(args.backup) if applied else None, "writes_raw_annotations": False}
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-8K Step13 最终审计与可选门禁失败 pair 删除")
    parser.add_argument("--annotations", type=Path, default=path("annotations"))
    parser.add_argument("--subsets", type=Path, default=path("annotations_subset"))
    parser.add_argument("--report", type=Path, default=Path("anno/step13_final_audit_report.json"))
    parser.add_argument("--backup", type=Path,
                        default=Path("anno/annotations_subset.json.step13_before_gate_delete.bak"))
    parser.add_argument("--apply", action="store_true", help="删除未通过 Step8 等价门禁的正/负互补 pair")
    parser.add_argument("--overwrite-backup", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        report = run(args)
        write_json_atomic(args.report, report)
        print(json.dumps({"applied": report["step13_apply"]["applied"],
                          "no_op": report["step13_apply"]["no_op"],
                          "planned_delete_pair_count": report["step13_apply"]["planned_delete_pair_count"],
                          "deleted_pair_count": report["step13_apply"]["deleted_pair_count"],
                          "remaining_gate_failures": report["step8_gate_audit"]["rejected_records"],
                          "remaining_shared_pairs": report["pair_audit"]["complementary_pairs_with_coordinate_overlap"],
                          "remaining_text_conflicts": len(report["same_text_different_point_set_conflicts"]),
                          "writes_raw_annotations": False,
                          "report": str(args.report)}, ensure_ascii=False, indent=2))
        if not args.apply:
            print("dry-run：未写入 annotations_subset.json；确认后加 --apply")
        elif report["step13_apply"]["no_op"]:
            print("无需删除：没有未通过等价门禁的互补 pair")
        return 0
    except Exception as error:
        print(f"❌ Step13 audit failed: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
