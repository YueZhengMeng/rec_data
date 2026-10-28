"""将最终审核子集与 step9 文本导出为独立的 REC-8K 风格标注。

annotations_subset 只含新子集，不混入原标注；每条记录保存 root_caption。
成员以最终子集 instance_ids 为准，从实例表及原标注校验坐标，不用子集记录中的
yes_points。原始 split 只用于只读校验和统计，不写新的划分文件。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from recsub.caption import select_caption_record
from recsub.io import iter_json_records, iter_jsonl

Root = tuple[str, str]


def load_nodes(source: Path, *, latest: bool = False) -> dict[str, dict]:
    """兼容 JSONL/格式化记录流/数组；latest 模式人工确认优先，否则取最后记录。"""
    nodes: dict[str, dict] = {}
    for record in iter_json_records(source):
        if not isinstance(record, dict):
            raise ValueError(f"{source} 的记录必须为 JSON 对象")
        if record.get("node_type") not in (None, "predicate_subset"):
            continue
        node_id = record.get("node_id") or record.get("review_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"{source} 含缺少 node_id/review_id 的记录")
        if node_id in nodes and not latest:
            raise ValueError(f"{source} 含重复子集 ID：{node_id}")
        nodes[node_id] = select_caption_record(nodes.get(node_id), record) if latest else record
    return nodes


def _read_json(source: Path) -> dict:
    with source.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{source} 顶层必须为 JSON 对象")
    return value


def _point(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and all(type(number) in (int, float) and math.isfinite(number) for number in value))


def validate_original(annotations: Mapping, splits: Mapping) -> dict[Root, str]:
    """split 按 (图片, 根 caption) 继承，不能单凭图片猜测。"""
    for image_name, captions in annotations.items():
        if not isinstance(image_name, str) or not isinstance(captions, dict):
            raise ValueError("原标注必须为 图片名 → caption → 标注对象")
        for caption, entry in captions.items():
            if (not isinstance(caption, str) or not isinstance(entry, dict)
                    or any(not isinstance(entry.get(field), str)
                           for field in ("class", "attribute", "type"))
                    or not isinstance(entry.get("points"), list)
                    or not all(_point(point) for point in entry["points"])):
                raise ValueError(f"原标注字段无效：{image_name}/{caption}")
    by_root: dict[Root, str] = {}
    by_image: dict[str, str] = {}
    if set(splits) != {"train", "val", "test"}:
        raise ValueError("原始 splits 必须只含 train/val/test")
    for split, pairs in splits.items():
        if not isinstance(pairs, list):
            raise ValueError(f"splits[{split!r}] 必须是 [图片名, caption] 数组")
        for pair in pairs:
            if (not isinstance(pair, list) or len(pair) != 2
                    or not all(isinstance(value, str) for value in pair)):
                raise ValueError(f"splits[{split!r}] 含无效条目：{pair!r}")
            key = tuple(pair)
            if key[0] not in annotations or key[1] not in annotations[key[0]]:
                raise ValueError(f"划分条目在原标注中不存在：{key}")
            if key in by_root:
                raise ValueError(f"原划分条目重复或跨划分：{key}")
            previous_split = by_image.setdefault(key[0], split)
            if previous_split != split:
                raise ValueError(f"原始图片跨 split：{key[0]} ({previous_split}, {split})")
            by_root[key] = split
    if set(by_image) != set(annotations):
        raise ValueError("原始 splits 未完整覆盖原标注中的图片")
    if len(by_root) != sum(len(records) for records in annotations.values()):
        raise ValueError("原始 splits 未完整覆盖原标注中的根 caption")
    return by_root


def _validate_export_images(annotations: Mapping, splits: Mapping) -> None:
    """校验本次导出的图片在每个 split 中只出现一次且完整覆盖。"""
    required = ("train", "val", "test")
    if tuple(splits) != required:
        raise ValueError(f"导出图片划分必须按顺序只含 train/val/test，实际为 {list(splits)}")
    seen: dict[str, str] = {}
    for split in required:
        images = splits[split]
        if not isinstance(images, list):
            raise ValueError(f"导出图片划分[{split!r}] 必须是图片名列表")
        for image in images:
            if not isinstance(image, str) or not image:
                raise ValueError(f"导出图片划分[{split!r}] 含无效图片名：{image!r}")
            if image not in annotations:
                raise ValueError(f"导出图片划分[{split!r}] 的图片没有子集标注：{image}")
            if image in seen:
                raise ValueError(f"导出图片跨 split 或重复：{image} ({seen[image]}, {split})")
            seen[image] = split
    if set(seen) != set(annotations):
        raise ValueError("导出图片划分未完整覆盖子集标注中的图片")


def subset_relations(subsets: Mapping[str, dict]) -> tuple[dict[str, str], dict[str, str]]:
    """从节点元数据建立正负身份和双向关联，不能从生成短语中的 not 猜测。"""
    polarities: dict[str, str] = {}
    partners: dict[str, str] = {}
    for node_id, subset in subsets.items():
        polarity = (subset.get("predicate") or {}).get("polarity")
        complement_of = subset.get("complement_of")
        if polarity not in ("positive", "negative"):
            raise ValueError(f"{node_id} 的谓词 polarity 无效：{polarity}")
        if complement_of is not None:
            if not isinstance(complement_of, str) or not complement_of:
                raise ValueError(f"{node_id} 的 complement_of 必须为非空节点 ID")
            if polarity == "positive":
                raise ValueError(f"{node_id} 的 complement_of 与 positive 极性冲突")
            polarities[node_id] = "no"
        elif polarity == "negative":
            raise ValueError(f"{node_id} 是负集但缺少 complement_of，无法关联正集")
        else:
            polarities[node_id] = "yes"
    for node_id, subset in subsets.items():
        if polarities[node_id] != "no":
            continue
        positive_id = subset["complement_of"]
        positive = subsets.get(positive_id)
        if positive is None or polarities[positive_id] != "yes":
            raise ValueError(f"{node_id} 的 complement_of 未指向有效正集：{positive_id}")
        if any(subset.get(key) != positive.get(key) for key in ("image_name", "caption", "category")):
            raise ValueError(f"{node_id} 的补集不属于同图、同根 caption 或同类别")
        if positive_id in partners:
            raise ValueError(f"正集 {positive_id} 被多个负集关联，无法唯一指定补集")
        # 最终审核点集是权威结果；这里只建立关联，不重算补集或额外处理 unknown。
        partners[node_id] = positive_id
        partners[positive_id] = node_id
    return polarities, partners


def build_rec8k_subsets(*, annotations_path: Path, splits_path: Path, subsets_path: Path,
                        captions_path: Path, instances_path: Path) -> tuple[dict, dict]:
    """纯数据变换：只返回新子集标注和统计，图片/根 caption 顺序沿用原标注。"""
    original = _read_json(annotations_path)
    original_splits = _read_json(splits_path)
    root_splits = validate_original(original, original_splits)
    subsets = load_nodes(subsets_path)
    polarities, partners = subset_relations(subsets)
    captions = load_nodes(captions_path, latest=True)
    image_order = {image: index for index, image in enumerate(original)}
    root_order = {(image, caption): index for image, records in original.items()
                  for index, caption in enumerate(records)}
    counters = Counter(final_subsets=len(subsets), caption_records=len(captions))
    skipped: list[dict] = []
    work: list[tuple[str, dict, dict]] = []
    wanted_ids: set[int] = set()
    for node_id, subset in subsets.items():
        review = subset.get("review") or {}
        generated = captions.get(node_id)
        reason = None
        if review.get("discarded") or review.get("status") == "discard":
            reason = "discarded_subset"
        elif generated is None:
            reason = "missing_caption"
        elif generated.get("failed"):
            reason = "caption_failed"
        elif generated.get("accepted") is not True or generated.get("language_check") != "yes":
            reason = "language_not_accepted"
        if reason:
            counters[reason] += 1
            skipped.append({"node_id": node_id, "reason": reason})
            continue
        image, parent, category = (subset.get(key) for key in ("image_name", "caption", "category"))
        if not all(isinstance(value, str) and value.strip() for value in (image, parent, category)):
            raise ValueError(f"{node_id} 缺少 image_name/caption/category")
        key = (image, parent)
        if image not in original or parent not in original[image]:
            raise ValueError(f"{node_id} 的根 caption 不在原标注：{key}")
        if key not in root_splits:
            raise ValueError(f"{node_id} 的根 caption 不在原 splits：{key}")
        source = original[image][parent]
        if source["class"] != category:
            raise ValueError(f"{node_id} 的类别与原标注不一致")
        if any(generated.get(field) != subset[field] for field in ("image_name", "caption", "category")):
            raise ValueError(f"{node_id} 的 step9 文本与最终子集根 caption 不一致")
        if generated.get("original_attributes") != source["attribute"]:
            raise ValueError(f"{node_id} 的原属性与原标注不一致")
        display = (subset.get("predicate") or {}).get("display")
        if not isinstance(display, str) or generated.get("additional_attribute") != display:
            raise ValueError(f"{node_id} 的新增属性已改变，请重跑 step9")
        phrase = generated.get("new_attributes")
        if not isinstance(phrase, str) or not phrase.strip():
            raise ValueError(f"{node_id} 缺少有效 new_attributes")
        if generated.get("new_caption") != category + " " + phrase:
            raise ValueError(f"{node_id} 的 new_caption 不是类别原词 + 空格 + 新属性短语")
        ids = subset.get("instance_ids")
        if (not isinstance(ids, list) or not ids
                or any(type(value) is not int or value < 0 for value in ids)
                or len(ids) != len(set(ids))):
            raise ValueError(f"{node_id} 的 instance_ids 必须为非空、不重复的整数列表")
        wanted_ids.update(ids)
        work.append((node_id, subset, generated))
    counters["orphan_caption_records"] = len(set(captions) - set(subsets))
    instances: dict[int, dict] = {}
    if wanted_ids:
        for instance in iter_jsonl(instances_path):
            instance_id = instance.get("instance_id")
            if instance_id not in wanted_ids:
                continue
            if type(instance_id) is not int or instance_id in instances:
                raise ValueError(f"实例表含重复或无效 ID：{instance_id}")
            instances[instance_id] = instance
        missing = wanted_ids - set(instances)
        if missing:
            raise ValueError(f"实例表缺少子集成员：{sorted(missing)[:10]}")

    def order(item: tuple[str, dict, dict]) -> tuple:
        node_id, subset, _ = item
        index = subset.get("subset_index")
        return (image_order[subset["image_name"]],
                root_order[(subset["image_name"], subset["caption"])],
                index if type(index) is int else 2 ** 63, node_id)

    annotations: dict[str, dict] = {}
    exported_keys: dict[str, str] = {}
    splits: dict[str, list] = {split: [] for split in ("train", "val", "test")}
    image_splits: dict[str, str] = {}
    for node_id, subset, generated in sorted(work, key=order):
        image, parent, category = (subset[key] for key in ("image_name", "caption", "category"))
        source = original[image][parent]
        selected = []
        for instance_id in subset["instance_ids"]:
            instance = instances[instance_id]
            if any(instance.get(field) != subset[field] for field in ("image_name", "caption", "category")):
                raise ValueError(f"{node_id} 的成员 {instance_id} 不属于其根 caption")
            if instance.get("attributes") != source["attribute"]:
                raise ValueError(f"{node_id} 的成员 {instance_id} 原属性不匹配")
            index = instance.get("point_idx")
            if (type(index) is not int or not 0 <= index < len(source["points"])
                    or instance.get("point") != source["points"][index]):
                raise ValueError(f"{node_id} 的成员 {instance_id} 点序号/坐标与原标注不一致")
            selected.append((index, instance_id))
        selected.sort()
        if len({index for index, _ in selected}) != len(selected):
            raise ValueError(f"{node_id} 的不同成员对应重复 point_idx")
        caption = generated["new_caption"]
        image_records = annotations.setdefault(image, {})
        # caption 是原数据格式的字典键，重名必须显式解决，不能覆盖/合并丢失来源。
        if caption in image_records:
            raise ValueError(f"{node_id} 的新 caption 在同图重名，拒绝覆盖/合并：{image}/{caption}")
        image_records[caption] = {
            "class": category, "attribute": generated["new_attributes"],
            "points": [source["points"][index] for index, _ in selected],
            "type": source["type"], "root_caption": parent,
            "subset_polarity": polarities[node_id], "complement_caption": None,
        }
        exported_keys[node_id] = caption
        counters["positive_subsets" if polarities[node_id] == "yes" else "negative_subsets"] += 1
        split = root_splits[(image, parent)]
        if image in image_splits and image_splits[image] != split:
            raise ValueError(f"子集图片跨 split：{image}")
        if image not in image_splits:
            image_splits[image] = split
            splits[split].append(image)
        counters["exported_subsets"] += 1
        counters["string_check_no_exported"] += generated.get("string_check") == "no"
    # 第二遍绑定双方已导出的 caption；不依赖正负节点的输入/输出先后顺序。
    for node_id, caption in exported_keys.items():
        partner_caption = exported_keys.get(partners.get(node_id))
        annotations[subsets[node_id]["image_name"]][caption]["complement_caption"] = partner_caption
        counters["linked_subsets" if partner_caption is not None else "unlinked_subsets"] += 1
    # 图片列表去重、互斥且恰好覆盖本次导出的子集图片。
    _validate_export_images(annotations, splits)
    counters["exported_images"] = len(annotations)
    report = {"counts": dict(counters), "split_counts": {key: len(value) for key, value in splits.items()},
              "type_policy": "inherit_root_caption", "order_policy": "source_images_then_root_captions",
              "skipped": skipped}
    return annotations, report
