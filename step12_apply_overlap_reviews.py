"""Step12 apply：应用已确认的共享坐标审核，并只去重子集标注。

默认 dry-run；只有 ``--apply`` 才写回 ``anno/annotations_subset.json``。
原始 ``anno/annotations.json`` 在本步骤中始终只读。

    python step12_apply_overlap_reviews.py --dry-run
    python step12_apply_overlap_reviews.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import path
from recsub.console import configure_console
from recsub.extract import file_digest
from recsub.io import write_json_atomic
from recsub.subset_audit import (audit_pair_overlaps, duplicate_report, point_key,
                                 read_json, unique_points)
from step12_review_overlaps import build_review_items, load_states


def load_manifest(review_dir: Path) -> dict[str, Any]:
    manifest_path = review_dir / "review_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到审核 manifest：{manifest_path}；请先完成 step12 审核")
    manifest = read_json(manifest_path)
    if not isinstance(manifest.get("tasks"), list) or not manifest["tasks"]:
        raise ValueError("审核 manifest 缺少完整 tasks")
    return manifest


def task_fingerprint(raw_path: Path, subset_path: Path, task_ids: list[str]) -> str:
    payload = {"annotations_sha256": file_digest(raw_path),
               "subsets_sha256": file_digest(subset_path), "task_ids": task_ids}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def validate_manifest(manifest: Mapping[str, Any], raw_path: Path, subset_path: Path) -> None:
    """审核 manifest 必须对应当前输入；输入变化时拒绝套用既有结果。"""
    task_ids = [str(item.get("review_id") or "") for item in manifest["tasks"]]
    if any(not value for value in task_ids) or len(task_ids) != len(set(task_ids)):
        raise ValueError("审核 manifest 含无效或重复 review_id")
    if task_fingerprint(raw_path, subset_path, task_ids) != manifest.get("fingerprint"):
        raise ValueError("输入标注已改变或审核 manifest 不匹配；拒绝套用既有审核结果")


def apply_items(raw: dict[str, Any], subsets: dict[str, Any], items: Sequence[Mapping[str, Any]],
                states: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    changed_pairs = []
    for item in items:
        state = states.get(item["review_id"], {})
        if state.get("status") != "confirmed":
            raise ValueError(f"仍有配对未确认：{item['review_id']}")
        image = item["image_name"]
        yes_caption, no_caption, root = item["yes_caption"], item["_no_caption"], item["root_caption"]
        if image not in subsets or yes_caption not in subsets[image] or no_caption not in subsets[image]:
            raise ValueError(f"审核配对在当前子集文件中不存在：{image}/{yes_caption}")
        yes_record, no_record = subsets[image][yes_caption], subsets[image][no_caption]
        yes_points = {point_key(point) for point in yes_record.get("points", [])}
        no_points = {point_key(point) for point in no_record.get("points", [])}
        expected_shared = {point_key(point) for point in item["shared_points"]}
        shared = yes_points & no_points
        if shared != expected_shared:
            raise ValueError(f"当前共享点已改变：{image}/{yes_caption}")
        root_points = raw[image][root].get("points", [])
        ordered_root, _ = unique_points(root_points)
        root_keys = {point_key(point) for point in ordered_root}
        if yes_points | no_points != root_keys:
            raise ValueError(f"互补配对未覆盖 root：{image}/{root}")
        selected_indices = state.get("selected_yes_indices")
        if (not isinstance(selected_indices, list)
                or any(type(index) is not int or not 0 <= index < len(item["shared_points"])
                       for index in selected_indices)
                or len(selected_indices) != len(set(selected_indices))):
            raise ValueError(f"审核状态点索引无效：{item['review_id']}")
        selected_yes = {point_key(item["shared_points"][index]) for index in selected_indices}
        yes_keys = (yes_points - shared) | selected_yes
        no_keys = (no_points - shared) | (shared - selected_yes)
        if yes_keys & no_keys or yes_keys | no_keys != root_keys:
            raise ValueError(f"审核结果不能形成互补分区：{image}/{root}")
        yes_record["points"] = [point for point in ordered_root if point_key(point) in yes_keys]
        no_record["points"] = [point for point in ordered_root if point_key(point) in no_keys]
        changed_pairs.append({"review_id": item["review_id"], "image": image,
                              "root_caption": root, "yes_caption": yes_caption,
                              "no_caption": no_caption, "shared_count": len(shared),
                              "assigned_yes": len(selected_yes),
                              "assigned_no": len(shared - selected_yes),
                              "yes_count_after_pair": len(yes_record["points"]),
                              "no_count_after_pair": len(no_record["points"])})

    dedup_records = dedup_removed = 0
    for captions in subsets.values():
        for record in captions.values():
            points = record.get("points", [])
            unique, removed = unique_points(points)
            if removed:
                record["points"] = unique
                dedup_records += 1
                dedup_removed += removed

    final_audit = audit_pair_overlaps(subsets, raw)
    return {"reviewed_pair_count": len(changed_pairs),
            "reviewed_shared_coordinates": sum(row["shared_count"] for row in changed_pairs),
            "dedup_records_changed": dedup_records, "dedup_entries_removed": dedup_removed,
            "remaining_shared_pairs": final_audit["complementary_pairs_with_coordinate_overlap"],
            "coverage_mismatch_pairs": final_audit["complementary_pairs_with_coverage_mismatch"],
            "pairs": changed_pairs}


def no_op_report(raw_hash: str, subset_hash: str, *, dry_run: bool, reason: str) -> dict[str, Any]:
    return {"operation": "apply_confirmed_overlap_reviews_and_deduplicate_subset_only",
            "dry_run": dry_run, "no_op": True, "no_op_reason": reason,
            "reviewed_pair_count": 0, "reviewed_shared_coordinates": 0,
            "dedup_records_changed": 0, "dedup_entries_removed": 0,
            "remaining_shared_pairs": 0, "coverage_mismatch_pairs": 0, "pairs": [],
            "raw_sha256_before": raw_hash, "subset_sha256_before": subset_hash}


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw = read_json(args.annotations)
    subsets = read_json(args.subsets)
    raw_hash_before = file_digest(args.annotations)
    subset_hash_before = file_digest(args.subsets)
    if args.report.is_file():
        previous = read_json(args.report)
        if (previous.get("operation") == "apply_confirmed_overlap_reviews_and_deduplicate_subset_only"
                and previous.get("raw_sha256_after") == raw_hash_before
                and previous.get("subset_sha256_after") == subset_hash_before):
            previous["no_op"] = True
            return previous

    current_items = build_review_items(raw, subsets)
    if not current_items:
        # 已经没有共享坐标对：输入要么本就无需处理，要么正是上一次 apply 的产物。
        report = no_op_report(raw_hash_before, subset_hash_before, dry_run=not args.apply,
                              reason="no complementary pairs with shared coordinates")
        write_json_atomic(args.report, report)
        return report

    manifest = load_manifest(args.review_dir)
    validate_manifest(manifest, args.annotations, args.subsets)
    states = load_states(args.review_dir / "review_state.jsonl",
                         {str(item["review_id"]): item for item in current_items})
    pending = [item["review_id"] for item in current_items
               if states.get(item["review_id"], {}).get("status") != "confirmed"]
    if pending:
        raise ValueError(f"还有 {len(pending)} 个审核任务未确认，拒绝 apply")

    before_report = {"subset_duplicates": duplicate_report(subsets),
                     "raw_sha256": raw_hash_before, "subset_sha256": subset_hash_before}
    result = apply_items(raw, subsets, current_items, states)
    after_subset_hash = hashlib.sha256(json.dumps(subsets, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest()
    report = {"operation": "apply_confirmed_overlap_reviews_and_deduplicate_subset_only",
              "dry_run": not args.apply, "raw_annotations_path": str(args.annotations),
              "subset_annotations_path": str(args.subsets), "raw_sha256_before": raw_hash_before,
              "subset_sha256_before": subset_hash_before, "preview_subset_content_sha256": after_subset_hash,
              "review_task_hash_verified": True,
              "before": before_report, **result}
    if args.apply:
        if args.backup.exists() and not args.overwrite_backup:
            raise FileExistsError(f"备份已存在，拒绝覆盖：{args.backup}；请另指定 --backup 或加 --overwrite-backup")
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.subsets, args.backup)
        write_json_atomic(args.subsets, subsets)
        raw_hash_after = file_digest(args.annotations)
        subset_hash_after = file_digest(args.subsets)
        if raw_hash_after != raw_hash_before:
            raise RuntimeError("严重错误：apply 修改了原始 annotations.json")
        report["raw_sha256_after"] = raw_hash_after
        report["subset_sha256_after"] = subset_hash_after
        report["backup"] = str(args.backup)
    write_json_atomic(args.report, report)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="应用 step12 共享坐标审核，并只去重 annotations_subset")
    parser.add_argument("--annotations", type=Path, default=path("annotations"))
    parser.add_argument("--subsets", type=Path, default=path("annotations_subset"))
    parser.add_argument("--review-dir", type=Path, default=Path("review/overlaps"))
    parser.add_argument("--report", type=Path, default=Path("anno/step12_apply_overlap_report.json"))
    parser.add_argument("--backup", type=Path, default=Path("anno/annotations_subset.json.bak"))
    parser.add_argument("--apply", action="store_true", help="明确写回 annotations_subset.json")
    parser.add_argument("--dry-run", action="store_true", help="显式请求预览模式（默认行为）")
    parser.add_argument("--overwrite-backup", action="store_true", help="允许替换已有备份")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        if not args.annotations.is_file() or not args.subsets.is_file():
            raise FileNotFoundError("annotations 或 annotations_subset 不存在")
        report = run(args)
        print(json.dumps({key: report[key] for key in (
            "operation", "dry_run", "reviewed_pair_count", "reviewed_shared_coordinates",
            "dedup_records_changed", "dedup_entries_removed", "remaining_shared_pairs",
            "coverage_mismatch_pairs", "raw_sha256_before", "subset_sha256_before")},
                         ensure_ascii=False, indent=2))
        print(f"报告 → {args.report}")
        if report.get("no_op"):
            print(f"无需处理：{report.get('no_op_reason', '当前输入已应用过审核结果')}")
        elif not args.apply:
            print("dry-run：未写入 annotations_subset.json；请确认后加 --apply")
        return 0
    except Exception as error:
        print(f"❌ Step12 apply failed: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
