"""step9 共用的子集输入、属性合成解析、字符串检查和续跑。

一条输出对应一个子集，保留原始 caption、谓词、点集及审核信息。
字符串包含是诊断项，不是语言审核的硬门禁；只有有效且 accepted=yes 的结果
算作完成，审核 no、未审核和失败记录重跑时都会再次提交。内部 instance_id 仅用于
复用 batch/rank 工具，不写入输出。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from recsub.io import check_or_claim_run_fingerprint, iter_jsonl
from recsub.prompts import build_caption_review_prompt, build_compose_caption_prompt


def load_caption_work(subsets: Path, instances: Path, *, completed: set[str],
                      image_names: set[str] | None = None, limit: int = 0) -> list[dict]:
    """用父根的原始 attributes，而不是开放提取属性或从 caption 猜词。"""
    nodes: dict[str, dict] = {}
    for node in iter_jsonl(subsets):
        if node.get("node_type") not in (None, "predicate_subset"):
            continue
        if image_names is not None and node.get("image_name") not in image_names:
            continue
        review = node.get("review") or {}
        if review.get("discarded") or review.get("status") == "discard":
            continue
        node_id = node.get("node_id") or node.get("review_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("子集缺少有效的 node_id/review_id")
        if node_id in nodes:
            raise ValueError(f"子集 ID 重复：{node_id}")
        if any(not isinstance(node.get(key), str) or not node[key].strip()
               for key in ("image_name", "caption", "category")):
            raise ValueError(f"{node_id} 缺少 image_name/caption/category")
        predicate = node.get("predicate")
        display = predicate.get("display") if isinstance(predicate, Mapping) else None
        if not isinstance(display, str) or not display.strip():
            raise ValueError(f"{node_id} 缺少 predicate.display，不能猜测新增属性")
        # display 是完整表面形式，包含 verb/not；不能用裸 value 或再拼一次 not。
        nodes[node_id] = {"source": node, "instance_id": node_id,
                          "additional_attribute": display}

    items = list(nodes.values())
    # 先截取再排除完成 ID，保证 --limit 的续跑范围不漂移。
    if limit:
        items = items[:limit]
    items = [item for item in items if item["instance_id"] not in completed]
    roots = {(item["source"]["image_name"], item["source"]["caption"]) for item in items}
    parents: dict[tuple[str, str], tuple[str, str]] = {}
    if roots:
        for instance in iter_jsonl(instances):
            key = (instance["image_name"], instance["caption"])
            if key not in roots:
                continue
            attributes = instance.get("attributes")
            category = instance.get("category")
            if not isinstance(attributes, str) or not isinstance(category, str):
                raise ValueError(f"父根 {key} 的 category/attributes 必须是字符串")
            value = (category, attributes)
            if key in parents and parents[key] != value:
                raise ValueError(f"父根 {key} 的 category/attributes 不一致")
            parents[key] = value
    for item in items:
        source = item["source"]
        key = (source["image_name"], source["caption"])
        if key not in parents:
            raise ValueError(f"实例表中找不到父根：{key}")
        category, attributes = parents[key]
        if category != source["category"]:
            raise ValueError(f"{item['instance_id']} 的类别与父根不一致")
        item.update(category=category, caption=source["caption"], attributes=attributes)
    return items


def compose_prompt(item: Mapping[str, Any]) -> str:
    return build_compose_caption_prompt(
        item["category"], item["attributes"], item["additional_attribute"],
    )


def review_prompt(item: Mapping[str, Any], generated: Mapping[str, str]) -> str:
    return build_caption_review_prompt(
        item["category"], item["attributes"], item["additional_attribute"], generated,
    )


def parse_attribute_phrase(content: str) -> str | None:
    """Parse the first-stage plain-text response, not a JSON caption object."""
    if not isinstance(content, str):
        return None
    phrase = content.strip()
    fenced = re.fullmatch(r"```(?:text|plain)?\s*(.*?)\s*```", phrase,
                          flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        phrase = fenced.group(1).strip()
    # A quoted single string is recoverable, but JSON objects/lists and explanations are not.
    if len(phrase) >= 2 and phrase[0] == phrase[-1] and phrase[0] in ('"', "'"):
        phrase = phrase[1:-1].strip()
    if not phrase or len(phrase.splitlines()) != 1 or phrase.startswith(("{", "[", "```")):
        return None
    return phrase


def generated_caption(category: str, attributes: str) -> dict[str, str]:
    """Caption is constructed by code; never use a model-generated caption."""
    return {"new_attributes": attributes, "new_caption": category + " " + attributes}


def valid_generated_record(record: Mapping[str, Any]) -> bool:
    category, attributes = record.get("category"), record.get("new_attributes")
    return (isinstance(category, str) and bool(category.strip())
            and isinstance(attributes, str) and bool(attributes.strip())
            and record.get("new_caption") == category + " " + attributes)


def accepted_caption(record: Mapping[str, Any]) -> bool:
    return (not record.get("failed") and record.get("accepted") is True
            and record.get("language_check") == "yes" and valid_generated_record(record))


def select_caption_record(previous: dict | None, current: dict) -> dict:
    """人工确认优先于模型历史；否则仍按最后一条模型记录取值。"""
    if previous is None:
        return current

    def manual_rank(record):
        review = record.get("manual_review")
        if (not accepted_caption(record) or not isinstance(review, Mapping)
                or review.get("status") != "yes"):
            return (False, "")
        return (True, str(review.get("reviewed_at") or ""))

    old_rank, new_rank = manual_rank(previous), manual_rank(current)
    return previous if old_rank > new_rank else current


def parse_language_review(content: str) -> str | None:
    # 不把 “yes, but ...” 或任意解释误当作通过。
    answer = content.strip().lower()
    return answer if answer in ("yes", "no") else None


def string_checks(item: Mapping[str, Any], generated: Mapping[str, str]) -> dict[str, str]:
    """严格 Python in（大小写敏感，不词干化）；空的原属性不增加限制。"""
    phrase = generated["new_attributes"]
    original = item["attributes"] in phrase
    additional = item["additional_attribute"] in phrase
    return {"original_attributes_in_new": "yes" if original else "no",
            "additional_attribute_in_new": "yes" if additional else "no",
            "string_check": "yes" if original and additional else "no"}


def caption_record(item: Mapping[str, Any], *, generated: dict | None = None,
                   language_check: str | None = None, reason: str | None = None,
                   generation_usage: Mapping | None = None,
                   review_usage: Mapping | None = None) -> dict:
    record = {**item["source"], "node_id": item["instance_id"],
              "original_attributes": item["attributes"],
              "additional_attribute": item["additional_attribute"]}
    if generated is not None:
        record.update(generated)
        record.update(string_checks(item, generated))
    record["language_check"] = language_check
    record["accepted"] = generated is not None and language_check == "yes" and not reason
    if reason:
        record.update(failed=True, reason=reason)
    for name, usage in (("generation_usage", generation_usage), ("review_usage", review_usage)):
        if usage:
            record[name] = dict(usage)
    for token in ("input_tokens", "output_tokens"):
        values = [usage[token] for usage in (generation_usage, review_usage)
                  if usage and usage.get(token) is not None]
        if values:
            record[token] = sum(values)
    return record


def completed_captions(output: Path, fingerprint: str, *, claim: bool = True,
                       check_fingerprint: bool = True) -> set[str]:
    """跳过已通过结果；人工确认优先，模型结果按最后一条判断。"""
    latest: dict[str, dict] = {}
    written = 0
    if output.is_file():
        for record in iter_jsonl(output):
            written += 1
            if (not isinstance(record.get("node_id"), str)
                    or any(key not in record for key in ("caption", "category", "image_name"))):
                raise ValueError(f"{output} 含不兼容的记录，请指定新的 --output")
            node_id = record["node_id"]
            latest[node_id] = select_caption_record(latest.get(node_id), record)
    completed = {node_id for node_id, record in latest.items() if accepted_caption(record)}
    check_or_claim_run_fingerprint(
        output, fingerprint, has_records=bool(written), claim=claim,
        check_fingerprint=check_fingerprint,
        mismatch_message=f"{output} 属于其他合成配置；请指定新的 --output",
    )
    return completed


def validate_paths(args) -> None:
    """禁止把追加输出指向输入，避免不可逆地污染标注。"""
    for name in ("subsets", "instances"):
        value = getattr(args, name)
        if not value.is_file():
            raise FileNotFoundError(f"找不到 {name}：{value}")
        if value.resolve() == args.output.resolve():
            raise ValueError("--output 不能与输入文件相同")
    if args.batch_size < 1 or args.limit < 0:
        raise ValueError("batch-size 必须 ≥1；limit 必须 ≥0")
