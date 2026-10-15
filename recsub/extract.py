"""step3 在线与本地入口共用的选择、解析、结果记录与续跑校验。

结果记录只保存逐实例的数据；运行指纹是整次运行的常量，旁挂在输出文件旁的
``.run.json`` 标记里（见 :func:`recsub.io.run_marker_path`），续跑时比对标记。
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping

from recsub.io import (
    check_or_claim_run_fingerprint, iter_jsonl, write_json_atomic,
)
from recsub.utils import match_json

ALLOWED_FIELDS = ("appearance", "action", "location", "relation")


def parse_attributes(content: str) -> dict[str, list[str]] | None:
    """两端均用 match_json 提取/修复 JSON，再校验属性字段和类型。"""
    parsed = match_json(content)
    if (not isinstance(parsed, dict) or not set(parsed)
            or not set(parsed) <= set(ALLOWED_FIELDS)):
        return None
    if any(not isinstance(value, list) or any(not isinstance(item, str) for item in value)
           for value in parsed.values()):
        return None
    return {field: [item.strip() for item in parsed.get(field, []) if item.strip()]
            for field in ALLOWED_FIELDS if field in parsed}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_fingerprint(settings: Mapping[str, Any]) -> str:
    encoded = json.dumps(settings, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extraction_record(instance: Mapping[str, Any], *, attributes: dict | None = None,
                      usage: Mapping[str, Any] | None = None, reason: str | None = None) -> dict:
    """一条提取结果。后端与运行配置不写进记录，它们属于输出文件旁的运行标记。"""
    record = {key: instance[key] for key in ("instance_id", "image_name", "caption", "category")}
    if attributes is None:
        record.update(failed=True, reason=reason or "属性解析失败")
    else:
        record["attributes"] = attributes
    if usage:
        record.update(usage)
    return record


def completed_ids(output: Path, fingerprint: str, *, claim: bool = True,
                  check_fingerprint: bool = True) -> set[int]:
    """读已完成实例并按需校验运行指纹。

    ``check_fingerprint=False`` 只绕过旁挂标记的校验，不放宽 JSONL 记录校验；
    空输出仅在没有既有标记时才会被认领。``claim=True`` 时，严格模式下的空输出
    认领行为与默认续跑流程相同。
    """
    completed: set[int] = set()
    written = 0
    if output.is_file():
        # 不忽略损坏行：静默跳过会使续跑混入不同配置的结果。
        for record in iter_jsonl(output):
            written += 1
            if (not isinstance(record.get("instance_id"), int)
                    or any(field not in record for field in ("image_name", "caption", "category"))):
                raise ValueError(f"{output} 含不兼容的记录，请指定新的 --output")
            if not record.get("failed") and isinstance(record.get("attributes"), Mapping):
                completed.add(record["instance_id"])
    check_or_claim_run_fingerprint(
        output,
        fingerprint,
        has_records=bool(written),
        check_fingerprint=check_fingerprint,
        claim=claim,
        mismatch_message=f"{output} 属于其他提取配置；请指定新的 --output",
    )
    return completed


def select_work(instance_path: Path, *, image_names: set[str] | None,
                sample_per_category: int, seed: int, completed: set[int],
                sample_output: Path | None) -> tuple[set[int], dict[str, int]]:
    """先在完整实例集合上抽样，再排除成功 ID；中断后选样不会漂移。"""
    by_category: dict[str, list[int]] = defaultdict(list)
    for instance in iter_jsonl(instance_path):
        if image_names is not None and instance["image_name"] not in image_names:
            continue
        by_category[instance["category"]].append(instance["instance_id"])

    if sample_per_category <= 0:
        sampled = {instance_id for ids in by_category.values() for instance_id in ids}
    else:
        rng = random.Random(seed)
        sampled = set()
        for category in sorted(by_category):
            ids = by_category[category]
            sampled.update(ids if len(ids) <= sample_per_category
                           else rng.sample(ids, sample_per_category))

    if sample_output is not None and sample_per_category > 0:
        manifest = {
            "seed": seed, "sample_per_category": sample_per_category,
            "image_names": sorted(image_names) if image_names else None,
            "instance_ids": sorted(sampled),
        }
        if sample_output.is_file():
            with sample_output.open(encoding="utf-8") as handle:
                if json.load(handle) != manifest:
                    raise ValueError(f"{sample_output} 的抽样设置不同；请指定独立的抽样文件")
        else:
            write_json_atomic(sample_output, manifest)
    return sampled - completed, {
        "categories": len(by_category),
        "candidates": sum(map(len, by_category.values())),
        "sampled": len(sampled) if sample_per_category > 0 else 0,
    }


def iter_selected(instance_path: Path, selected: set[int]) -> Iterator[dict]:
    for instance in iter_jsonl(instance_path):
        if instance["instance_id"] in selected:
            yield instance
