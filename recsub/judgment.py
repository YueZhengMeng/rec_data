"""Backend-independent candidate loading and predicate-judgment helpers."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from recsub.io import check_or_claim_run_fingerprint, iter_jsonl
from recsub.normalize import flatten_candidates
from recsub.utils import match_json

LABELS = ("yes", "no", "unknown")


def completed_judgments(
        output: Path,
        fingerprint: str,
        *,
        claim: bool = True,
        check_fingerprint: bool = True,
) -> tuple[set[int], int]:
    """读已完成实例并按需校验运行指纹。

    结果文件为空或不存在时，默认把输出路径认领给当前配置（写入旁挂标记）；
    已有记录的实例 ID 只排除成功记录，失败记录仍会在重跑时尝试。关闭指纹校验
    时仍验证所有 JSONL 记录，且不读取或改动已经存在的标记。
    """
    output = Path(output)
    completed: set[int] = set()
    written = 0
    if output.is_file():
        for record in iter_jsonl(output):
            written += 1
            if not record.get("judgment_failed"):
                completed.add(int(record["instance_id"]))
    check_or_claim_run_fingerprint(
        output,
        fingerprint,
        has_records=bool(written),
        check_fingerprint=check_fingerprint,
        claim=claim,
        mismatch_message=f"{output} 属于其他候选或判断配置；请指定新的 --output",
    )
    return completed, written


def iter_candidate_entries(candidates_path: Path):
    """产出 ``(图片, caption, 记录)``；``.jsonl`` 为新格式，其余按旧嵌套 JSON 读。"""
    candidates_path = Path(candidates_path)
    if candidates_path.suffix.lower() == ".jsonl":
        for record in iter_jsonl(candidates_path):
            if not isinstance(record, Mapping):
                raise ValueError(f"{candidates_path} 的候选记录必须是对象")
            image_name = record.get("image_name")
            caption = record.get("caption")
            if image_name is None or caption is None:
                raise ValueError(
                    f"{candidates_path} 的候选记录缺少 image_name/caption：{record!r}")
            yield str(image_name), str(caption), record
        return

    with candidates_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError(f"{candidates_path} 顶层必须是对象")
    for image_name, captions in payload.items():
        if not isinstance(captions, Mapping):
            raise ValueError(f"{candidates_path} 图片 {image_name!r} 的 caption 数据必须是对象")
        for caption, entry in captions.items():
            if not isinstance(entry, Mapping):
                raise ValueError(f"{candidates_path} {image_name!r} / {caption!r} 的记录必须是对象")
            yield str(image_name), str(caption), entry


def load_caption_predicates(candidates_path: Path) -> tuple[dict, dict, dict]:
    """读候选并整理成「根 → 谓词 / 问句 / 问句→ID」。

    候选文件按后缀区分：``.jsonl`` 是 step4 现在的一行一条格式，``.json`` 是
    图片 → caption 的旧嵌套格式，两种都能直接读。
    """
    by_root: dict[tuple[str, str], list[dict]] = {}
    phrases_by_root: dict[tuple[str, str], tuple[str, ...]] = {}
    ids_by_display: dict[tuple[str, str], dict[str, list[str]]] = {}
    for image_name, caption, entry in iter_candidate_entries(candidates_path):
        key = (image_name, caption)
        if key in by_root:
            raise ValueError(f"{candidates_path} 出现重复的根：{key!r}")
        predicates = flatten_candidates(entry)
        by_root[key] = predicates
        display_map: dict[str, list[str]] = defaultdict(list)
        ordered: list[str] = []
        for predicate in predicates:
            display = str(predicate.get("display") or predicate.get("value") or "")
            if not display:
                continue
            if display not in display_map:
                ordered.append(display)
            display_map[display].append(str(predicate["predicate_id"]))
        phrases_by_root[key] = tuple(ordered)
        ids_by_display[key] = dict(display_map)
    return by_root, phrases_by_root, ids_by_display


def parse_labels(content: str, phrases: Sequence[str]) -> dict[str, str] | None:
    """用 match_json 修复三值列表；缺项/冲突记 unknown，格式错误返回 None。"""
    parsed = match_json(content)
    if not isinstance(parsed, dict) or not parsed or not set(parsed) <= set(LABELS):
        return None
    if any(not isinstance(values, list) or any(not isinstance(value, str) for value in values)
           for values in parsed.values()):
        return None

    assignments = {phrase: set() for phrase in phrases}
    for label, values in parsed.items():
        for value in values:
            if value in assignments:
                assignments[value].add(label)
    return {phrase: next(iter(labels)) if len(labels) == 1 else "unknown"
            for phrase, labels in assignments.items()}


def build_work(instances: Iterable[Mapping[str, Any]], phrases_by_root, completed: set[int],
               image_names: set[str] | None) -> dict[tuple[str, str], list[dict]]:
    """按根收集待判实例；只保留判断需要的字段。"""
    work: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for instance in instances:
        instance_id = instance["instance_id"]
        if instance_id in completed:
            continue
        if image_names is not None and instance["image_name"] not in image_names:
            continue
        key = (str(instance["image_name"]), str(instance["caption"]))
        if not phrases_by_root.get(key):
            continue
        work[key].append({
            "instance_id": instance_id,
            "image_name": instance["image_name"],
            "caption": instance["caption"],
            "category": instance["category"],
            "attributes": instance.get("attributes", ""),
            "point": instance["point"],
        })
    return dict(work)


def restrict_labels(record: Mapping[str, Any], keep: Sequence[str]) -> dict:
    labels = record.get("labels")
    if not isinstance(labels, Mapping):
        return dict(record)
    return {**record, "labels": {phrase: labels[phrase] for phrase in keep if phrase in labels}}


def expand_labels(record: Mapping[str, Any], ids_by_display: Mapping[str, list[str]]) -> dict:
    labels = record.get("labels")
    if not isinstance(labels, Mapping):
        return dict(record)
    expanded: dict[str, str] = {}
    for display, label in labels.items():
        for predicate_id in ids_by_display.get(display, ()):
            expanded[predicate_id] = label
    return {**record, "labels": expanded}


def ratio_prune(records: Iterable[Mapping[str, Any]], phrases: Sequence[str],
                yes_ratio: float, no_ratio: float, min_instances: int) -> tuple[list[str], list[str]]:
    """从探针标签中挑出几乎恒定的问句；返回 (保留, 剪掉)。"""
    tally = {phrase: Counter() for phrase in phrases}
    for record in records:
        labels = record.get("labels")
        if not isinstance(labels, Mapping):
            continue
        for phrase in phrases:
            label = labels.get(phrase)
            if label in LABELS:
                tally[phrase][label] += 1
    kept, pruned = [], []
    for phrase in phrases:
        counted = sum(tally[phrase][label] for label in LABELS)
        if counted < min_instances:
            kept.append(phrase)
            continue
        yes_share = tally[phrase]["yes"] / counted
        (pruned if yes_share >= yes_ratio or yes_share <= no_ratio else kept).append(phrase)
    return kept, pruned
