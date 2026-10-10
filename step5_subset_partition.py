"""读取 judge_predicates.py 的实例三值结果，在每个原 caption 内划分层次子集。

yes 中的候选短语是子集条件，no 和 unknown 都不加入正子集。
候选已在 propose 阶段归并，三值标签不再做 embedding 合并，避免改变已判定的语义。
原始 instance_data.json 提供完整父点集；输出保留原 caption 和新增子集。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from tqdm import tqdm

from semantic_attributes import (
    ATTRIBUTE_FIELDS,
    DEFAULT_MODEL_PATH,
    DEFAULT_SIMILARITY_THRESHOLD,
    _compatible_phrases,
    _cosine,
    _instance_attribute_entries,
    deduplicate_instance_attributes,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_INPUT_PATH = Path("anno/predicate_labels.json")
DEFAULT_INSTANCES_PATH = Path("anno/instance_data.json")
DEFAULT_OUTPUT_PATH = Path("anno/fine_grained_subsets.json")
DEFAULT_MIN_SUBSET_SIZE = 5
DEFAULT_MAX_CONDITION_DEPTH = 3


def _condition_id(field: str, members: tuple[str, ...]) -> str:
    payload = json.dumps([field, members], ensure_ascii=False, separators=(",", ":"), indent=4)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def report_cross_caption_attribute_matches(records: list[dict[str, Any]], encoder: Any,
                                           similarity_threshold: float,
                                           vectors: dict[str, tuple[float, ...]] | None = None) -> int:
    """打印同图实例增强属性与其它 caption 原始属性的语义重复项。

    当前 caption 的原始 attributes 不参与自我报告。此诊断只输出提示，不
    过滤或改写任何内容；同一个对照 caption 内相同原始短语先按文本去重。
    """
    caption_attributes: dict[tuple[Any, Any], set[str]] = defaultdict(set)
    for record in records:
        phrase = record["attributes"]
        if phrase:
            caption_attributes[(record["image_idx"], record["caption_idx"])].add(phrase)

    entries_by_instance = []
    all_phrases = set()
    for record in records:
        entries = _instance_attribute_entries(record)
        entries_by_instance.append((record, entries))
        all_phrases.update(entry["text"] for entry in entries)
    all_phrases.update(phrase for phrases in caption_attributes.values() for phrase in phrases)
    if vectors is None:
        vectors = encoder.encode(all_phrases)

    match_count = 0
    for record, entries in entries_by_instance:
        for entry in entries:
            for other_image_idx, other_caption_idx in sorted(caption_attributes):
                if other_image_idx != record["image_idx"] or other_caption_idx == record["caption_idx"]:
                    continue
                for original_phrase in sorted(caption_attributes[(other_image_idx, other_caption_idx)]):
                    if (_compatible_phrases(entry["text"], original_phrase)
                            and _cosine(vectors[entry["text"]], vectors[original_phrase])
                            >= similarity_threshold):
                        print(
                            f"⚠️ 同图跨 caption 属性重复：image_idx={record['image_idx']}，"
                            f"instance_id={record['instance_id']}，caption_idx={record['caption_idx']}，"
                            f"原 attributes={record['attributes']!r}，"
                            f"more_attributes.{entry['field']}={entry['phrase']!r} 与 "
                            f"caption_idx={other_caption_idx} 的 attributes={original_phrase!r}，"
                            f"cosine={_cosine(vectors[entry['text']], vectors[original_phrase]):.4f}"
                        )
                        match_count += 1
    return match_count


@dataclass(frozen=True)
class Condition:
    condition_id: str
    field: str
    source_fields: tuple[str, ...]
    phrases: tuple[str, ...]
    display: str
    support_mask: int

    @property
    def support(self) -> int:
        return self.support_mask.bit_count()


def cluster_conditions(records: list[dict[str, Any]], encoder: Any,
                       similarity_threshold: float,
                       vectors: dict[str, tuple[float, ...]] | None = None) -> list[Condition]:
    """在一个 caption 根内，将四个增强字段合并后聚为语义条件。

    原始 attributes 仍按 type 分组，避免将数据集原始标注与生成属性
    混为同一条件。四个 more_attributes 字段共用一个聚类池，因而
    不同字段里意思相同的短语会覆盖相同的条件。support_mask 仍是当前
    caption 根中命中该条件的实例集合，可直接做位运算求交集。
    """
    if "yes" in records[0]:
        # 闭集判断的短语已固定：严格按同一个词的 yes 建立支持集合，不再语义合并。
        phrase_masks = defaultdict(int)
        for index, record in enumerate(records):
            for phrase in record["yes"]:
                phrase_masks[phrase] |= 1 << index
        return [Condition(
            condition_id=phrase, field="more_attributes", source_fields=("yes",),
            phrases=(phrase,), display=phrase, support_mask=mask
        ) for phrase, mask in sorted(phrase_masks.items())]

    phrase_instances: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    phrase_fields: dict[str, set[str]] = defaultdict(set)
    for local_index, record in enumerate(records):
        raw = record["attributes"]
        if raw:
            field = "original_attribute:" + record.get("type", "unspecified")
            phrase_instances[field][raw].add(local_index)
            phrase_fields[raw].add(field)
        attributes = record.get("more_attributes", {})
        for field in ATTRIBUTE_FIELDS:
            for phrase in attributes.get(field, []):
                if phrase:
                    phrase_instances["more_attributes"][phrase].add(local_index)
                    phrase_fields[phrase].add(field)

    phrases = sorted({phrase for field_phrases in phrase_instances.values()
                      for phrase in field_phrases})
    vectors = vectors if vectors is not None else encoder.encode(phrases)

    conditions = []
    for field in sorted(phrase_instances):
        field_phrases = sorted(phrase_instances[field])
        # 同一个增强属性池只建一次矩阵；不同来源字段之间也参加相似度比较。
        vector_matrix = np.asarray([vectors[phrase] for phrase in field_phrases], dtype=np.float32)
        similarity_matrix = vector_matrix @ vector_matrix.T
        clusters: list[list[int]] = []
        for phrase_index, phrase in enumerate(field_phrases):
            matching = []
            # complete-link：新短语必须与簇内每个成员都达标，避免链式误合并。
            for cluster_index, cluster in enumerate(clusters):
                if all(_compatible_phrases(phrase, field_phrases[existing_index])
                       and similarity_matrix[phrase_index, existing_index] >= similarity_threshold
                       for existing_index in cluster):
                    matching.append(cluster_index)
            if matching:
                clusters[matching[0]].append(phrase_index)
            else:
                clusters.append([phrase_index])
        for cluster_indices in clusters:
            cluster = tuple(field_phrases[index] for index in cluster_indices)
            member_indices = set()
            for phrase in cluster:
                member_indices.update(phrase_instances[field][phrase])
            source_fields = tuple(sorted({source_field for phrase in cluster
                                          for source_field in phrase_fields[phrase]
                                          if field == "more_attributes" or source_field == field}))
            conditions.append(Condition(
                condition_id=_condition_id(field, cluster),
                field=field,
                source_fields=source_fields,
                phrases=cluster,
                display=min(cluster),
                support_mask=sum(1 << index for index in member_indices),
            ))
    return sorted(conditions, key=lambda item: (item.field, item.display, item.condition_id))


def _stable_id(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=4)
    return prefix + hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _root_node(records: list[dict[str, Any]]) -> dict[str, Any]:
    root_key = [records[0]["image_name"], records[0]["category"], records[0]["caption"]]
    return {
        "node_id": _stable_id("caption_", root_key),
        "node_type": "caption_root",
        "parent_ids": [],
        "image_name": root_key[0],
        "category": root_key[1],
        "caption": root_key[2],
        "support_count": len(records),
        "instance_ids": [row["instance_id"] for row in records],
        "points": [row["point"] for row in records],
        "conditions": [],
        "condition_paths": [],
    }


@dataclass(frozen=True)
class _CandidatePath:
    mask: int
    parent_mask: int
    conditions: tuple[Condition, ...]

    @property
    def additional_depth(self) -> int:
        """Number of generated attributes in this path (original attributes do not count)."""
        return sum(item.field == "more_attributes" for item in self.conditions)


def _candidate_paths(records: list[dict[str, Any]], encoder: Any,
                     similarity_threshold: float, min_subset_size: int,
                     max_condition_depth: int,
                     vectors: dict[str, tuple[float, ...]] | None) -> dict[int, dict[int, list[_CandidatePath]]]:
    """Enumerate genuine refinements, counting only additional attributes."""
    conditions = [item for item in cluster_conditions(records, encoder, similarity_threshold, vectors)
                  if min_subset_size <= item.support < len(records)]
    full_mask = (1 << len(records)) - 1
    candidates: dict[int, dict[int, list[_CandidatePath]]] = defaultdict(lambda: defaultdict(list))

    def visit(start: int, selected: tuple[Condition, ...], parent_mask: int) -> None:
        if sum(item.field == "more_attributes" for item in selected) >= max_condition_depth:
            return
        for index in range(start, len(conditions)):
            condition = conditions[index]
            mask = parent_mask & condition.support_mask
            if mask == parent_mask or mask.bit_count() < min_subset_size:
                continue
            path = _CandidatePath(mask, parent_mask, selected + (condition,))
            candidates[path.additional_depth][mask].append(path)
            visit(index + 1, path.conditions, mask)

    visit(0, (), full_mask)
    return candidates


def _node_from_path(records: list[dict[str, Any]], root_id: str,
                    path: _CandidatePath, parent_id: str) -> dict[str, Any]:
    indices = [index for index in range(len(records)) if path.mask & (1 << index)]
    instance_ids = [records[index]["instance_id"] for index in indices]
    descriptions = [{
        "condition_id": item.condition_id,
        "field": item.field,
        "source_fields": list(item.source_fields),
        "canonical_phrase": item.display,
        "source_phrases": list(item.phrases),
    } for item in path.conditions]
    return {
        "node_id": _stable_id("subset_", [root_id, instance_ids]),
        "node_type": "attribute_subset",
        "parent_ids": [parent_id],
        "image_name": records[0]["image_name"],
        "category": records[0]["category"],
        "caption": records[0]["caption"],
        "support_count": len(indices),
        "instance_ids": instance_ids,
        "points": [records[index]["point"] for index in indices],
        "conditions": descriptions,
        "condition_paths": [descriptions],
    }


def _select_image_trees(groups: list[list[dict[str, Any]]], encoder: Any,
                        similarity_threshold: float, min_subset_size: int,
                        max_condition_depth: int,
                        vectors: dict[str, tuple[float, ...]] | None = None
                        ) -> list[dict[str, Any]]:
    """枚举每个可区分的条件交集；同一父集下的子集允许互相重叠。"""
    if min_subset_size < 1 or max_condition_depth < 1:
        raise ValueError("min_subset_size and max_condition_depth must be at least 1")
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be in [0, 1]")
    if not groups or any(not group for group in groups):
        raise ValueError("caption groups must not be empty")

    trees = []
    paths_by_group = []
    root_points: dict[tuple[float, float], int] = {}
    root_instances: dict[Any, int] = {}
    for group_index, group in enumerate(groups):
        root = _root_node(group)
        for record in group:
            point = tuple(record["point"])
            instance_id = record["instance_id"]
            if ((point in root_points and root_points[point] != group_index)
                    or (instance_id in root_instances and root_instances[instance_id] != group_index)):
                raise ValueError(f"同图不同 caption 的原始标注存在重合: {root['image_name']} / {root['caption']}")
            root_points[point] = root_instances[instance_id] = group_index
        trees.append({"root_id": root["node_id"], "nodes": [root]})
        paths_by_group.append(_candidate_paths(
            group, encoder, similarity_threshold, min_subset_size, max_condition_depth, vectors
        ))

    # 每个点集只输出一次（同一个 mask 可能在多个层出现），但不同点集之间不再
    # 竞争：一个实例可以同时属于多个子集，因此不做同层互斥筛选。
    selected_masks = [{(1 << len(group)) - 1: tree["root_id"]}
                      for group, tree in zip(groups, trees)]
    for depth in range(0, max_condition_depth + 1):
        for group_index, paths_by_depth in enumerate(paths_by_group):
            group = groups[group_index]
            # 先输出支持较多的子集，使父集通常排在子集前面；同一 mask 的等价路径按条件稳定选取。
            for mask, paths in sorted(paths_by_depth.get(depth, {}).items(),
                                      key=lambda item: (-item[0].bit_count(), item[0])):
                if mask in selected_masks[group_index]:
                    continue
                path = min(paths, key=lambda candidate: tuple(
                    (item.display, item.condition_id) for item in candidate.conditions
                ))
                # 父节点一定在更浅的层生成；找不到时说明该子集无法挂到树上，直接跳过。
                parent_id = selected_masks[group_index].get(path.parent_mask)
                if parent_id is None:
                    continue
                node = _node_from_path(group, trees[group_index]["root_id"], path, parent_id)
                trees[group_index]["nodes"].append(node)
                selected_masks[group_index][mask] = node["node_id"]
    return trees


def build_subset_tree(records: list[dict[str, Any]], encoder: Any,
                      similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
                      min_subset_size: int = DEFAULT_MIN_SUBSET_SIZE,
                      max_condition_depth: int = DEFAULT_MAX_CONDITION_DEPTH,
                      vectors: dict[str, tuple[float, ...]] | None = None) -> dict[str, Any]:
    """Build one caption's tree (use _select_image_trees for image-wide trees)."""
    return _select_image_trees([records], encoder, similarity_threshold,
                               min_subset_size, max_condition_depth, vectors)[0]


def group_image_records(records: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    """依照输入顺序按 image_idx 逐图聚集记录，以便检查同图其它 caption。"""
    current_image_idx = None
    current = []
    for record in records:
        image_idx = record["image_idx"]
        if image_idx != current_image_idx:
            if current:
                yield current
            current_image_idx = image_idx
            current = []
        current.append(record)
    if current:
        yield current


def group_caption_records(records: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    """按输入顺序收集连续的 image/category/caption 根集合。

    前置数据处理已保证相同根的记录连续，因此这里不再维护 seen_groups，
    也不再对重复根或非连续根做审核；读到 key 变化时直接结束上一组。
    """
    current_key = None
    current = []
    for record in records:
        key = (record["image_name"], record["category"], record["caption"])
        if key != current_key:
            if current:
                yield current
            current_key = key
            current = []
        current.append(record)
    if current:
        yield current


def _subset_more_attributes(node: dict[str, Any]) -> list[str]:
    """取子集新增的公共属性，不重复把原始 attribute 写入 more_attributes。"""
    result = []
    seen = set()
    for condition in node["conditions"]:
        if condition["field"] != "more_attributes":
            continue
        phrase = condition["canonical_phrase"]
        if phrase not in seen:
            seen.add(phrase)
            result.append(phrase)
    return result


def _annotation_entry(group: list[dict[str, Any]], points: list[list[float]],
                      more_attributes: list[str] | None = None) -> dict[str, Any]:
    """把一个原始 caption 根或生成子集转成 annotations_format 结构。"""
    first = group[0]
    entry = {
        "class": first["category"],
        "attribute": first["attributes"],
        "points": points,
        "type": first.get("type"),
    }
    if more_attributes is not None:
        entry["more_attributes"] = more_attributes
    return entry


def _unique_annotation_key(image_annotations: dict[str, Any], base_key: str,
                           node_id: str) -> str:
    """按用户指定的 key 生成子集名，极少数冲突时追加稳定 node_id。"""
    if base_key not in image_annotations:
        return base_key
    return f"{base_key} [{node_id}]"


def build_annotation_image(groups: list[list[dict[str, Any]]],
                           trees: list[dict[str, Any]]) -> dict[str, Any]:
    """将一个图片内的原始 caption 和生成子集写成数据集格式。"""
    image_annotations: dict[str, Any] = {}
    for group, tree in zip(groups, trees):
        caption = group[0]["caption"]
        image_annotations[caption] = _annotation_entry(
            group, [record["point"] for record in group]
        )
        for node in tree["nodes"][1:]:
            more_attributes = _subset_more_attributes(node)
            # 没有新增 more_attributes 的节点不写入数据集层级，避免仅由
            # 原始 attribute 产生一个无法区别于原 caption 的冗余 key。
            if not more_attributes:
                continue
            base_key = " ".join(part for part in (
                group[0]["category"], group[0]["attributes"], " ".join(more_attributes)
            ) if part).strip()
            key = _unique_annotation_key(image_annotations, base_key, node["node_id"])
            annotation = _annotation_entry(group, node["points"], more_attributes)
            if "yes" in group[0]:
                # unknown/未判实例不进入 yes；有这些项时仅是已确认的正部分，不宣称完整。
                annotation["label_source"] = "model_pseudo"
                annotation["complete"] = all(
                    not record.get("judgment_missing", False)
                    and all(phrase in record["yes"] or phrase in record["no"]
                            for phrase in more_attributes)
                    for record in group
                )
            image_annotations[key] = annotation

    return image_annotations


def attach_judgments(instances: list[dict], results: list[dict]) -> list[dict]:
    """保留已判断 caption 的完整原点集；未完成的实例没有 yes 条件。"""
    by_id = {row["instance_id"]: row for row in results}
    roots = {(row["image_name"], row["caption"]) for row in results}
    records = []
    for instance in instances:
        if (instance["image_name"], instance["caption"]) not in roots:
            continue
        judgment = by_id.get(instance["instance_id"], {})
        records.append({**instance, "yes": judgment.get("yes", []),
                        "no": judgment.get("no", []),
                        "unknown": judgment.get("unknown", []),
                        "judgment_missing": not judgment})
    return records


def run_partition(input_path: str | Path, output_path: str | Path, encoder: Any,
                  similarity_threshold: float, min_subset_size: int,
                  max_condition_depth: int, overwrite: bool = False,
                  metadata_output_path: str | Path | None = None,
                  instances_path: str | Path | None = None) -> dict[str, int]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    metadata_path = Path(metadata_output_path) if metadata_output_path else output_path.with_name(
        f"{output_path.stem}_metadata{output_path.suffix}"
    )
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入文件: {input_path}")
    if input_path.resolve() in {output_path.resolve(), metadata_path.resolve()}:
        raise ValueError("输入路径不能与任一输出路径相同")
    if output_path.resolve() == metadata_path.resolve():
        raise ValueError("数据输出和 metadata 输出不能使用同一路径")
    if not overwrite:
        for path in (output_path, metadata_path):
            if path.exists():
                raise FileExistsError(f"输出文件已存在，为防止覆盖已停止: {path} (可显式传入 --overwrite)")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as file:
        all_records = json.load(file)
    if instances_path is not None:
        if any("yes" not in row or "no" not in row or "unknown" not in row for row in all_records):
            raise ValueError("输入应为包含 yes/no/unknown 三个列表的实例判断结果")
        with open(instances_path, encoding="utf-8") as file:
            all_records = attach_judgments(json.load(file), all_records)
    image_groups = group_image_records(all_records)
    stats = {"instances": 0, "caption_roots": 0, "subset_nodes": 0, "images": 0}
    data_fd, data_temp_name = tempfile.mkstemp(
        prefix=output_path.name + ".", suffix=".tmp", dir=output_path.parent
    )
    metadata_fd, metadata_temp_name = tempfile.mkstemp(
        prefix=metadata_path.name + ".", suffix=".tmp", dir=metadata_path.parent
    )
    try:
        with os.fdopen(data_fd, "w", encoding="utf-8") as data_output:
            data_output.write("{")
            first_image = True
            for image_records in tqdm(image_groups):
                caption_groups = list(group_caption_records(image_records))
                image_vectors = None
                if "yes" not in image_records[0]:
                    # 保留旧算法的函数接口供历史测试使用；新入口只读取三值结果。
                    image_phrases = {phrase for record in image_records
                                     for phrase in [record["attributes"]] + [
                                         item for field in ATTRIBUTE_FIELDS
                                         for item in record["more_attributes"].get(field, [])
                                     ] if phrase}
                    image_vectors = encoder.encode(image_phrases)
                    report_cross_caption_attribute_matches(
                        image_records, encoder, similarity_threshold, image_vectors
                    )
                    for group in caption_groups:
                        for record in group:
                            record["more_attributes"] = deduplicate_instance_attributes(
                                record, encoder, similarity_threshold, image_vectors
                            )
                trees = _select_image_trees(
                    caption_groups, encoder, similarity_threshold,
                    min_subset_size, max_condition_depth, image_vectors
                )
                for group, tree in zip(caption_groups, trees):
                    stats["instances"] += len(group)
                    stats["caption_roots"] += 1
                    stats["subset_nodes"] += len(tree["nodes"]) - 1
                    print(
                        f"根 caption {stats['caption_roots']}：{group[0]['image_name']} / "
                        f"{group[0]['caption']}，实例 {len(group)}，"
                        f"子集 {len(tree['nodes']) - 1}"
                    )

                image_annotations = build_annotation_image(caption_groups, trees)
                image_name = image_records[0]["image_name"]
                if not first_image:
                    data_output.write(",")
                data_output.write("\n")
                json.dump(image_name, data_output, ensure_ascii=False, indent=4)
                data_output.write(":")
                json.dump(image_annotations, data_output, ensure_ascii=False, separators=(",", ":"), indent=4)
                first_image = False
                stats["images"] += 1
                del image_vectors
            data_output.write("\n}\n")
            data_output.flush()
            os.fsync(data_output.fileno())

        metadata = {
            "metadata": {
                "model_path": str(getattr(encoder, "model_path", DEFAULT_MODEL_PATH)) if encoder is not None else None,
                "condition_source": "yes" if instances_path is not None else "more_attributes",

                "similarity_threshold": similarity_threshold,
                "min_subset_size": min_subset_size,
                "max_condition_depth": max_condition_depth,
            },
            "stats": stats,
        }
        with os.fdopen(metadata_fd, "w", encoding="utf-8") as metadata_output:
            json.dump(metadata, metadata_output, ensure_ascii=False, indent=4)
            metadata_output.write("\n")
            metadata_output.flush()
            os.fsync(metadata_output.fileno())

        if overwrite:
            os.replace(data_temp_name, output_path)
            os.replace(metadata_temp_name, metadata_path)
        else:
            try:
                os.link(data_temp_name, output_path)
                os.link(metadata_temp_name, metadata_path)
            except FileExistsError:
                raise FileExistsError("输出文件在写入过程中已存在，为防止覆盖已停止")
            finally:
                for temp_name in (data_temp_name, metadata_temp_name):
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
    except Exception:
        for temp_name in (data_temp_name, metadata_temp_name):
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        raise
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于实例三值判断 yes 列表的层次子集划分")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="实例 yes/no/unknown 判断 JSON")
    parser.add_argument("--instances", type=Path, default=DEFAULT_INSTANCES_PATH, help="完整原始实例点集")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH,
                        help="annotations_format 风格的数据集输出 JSON")
    parser.add_argument("--metadata-output", type=Path, default=None,
                        help="metadata/stats 输出 JSON；默认与数据输出同目录并追加 _metadata")
    parser.add_argument("--min-subset-size", type=int, default=DEFAULT_MIN_SUBSET_SIZE,
                        help="保留子集的最少实例数")
    parser.add_argument("--max-condition-depth", type=int, default=DEFAULT_MAX_CONDITION_DEPTH,
                        help="最多新增 more_attributes 的数量（不包含原始 attribute）")
    parser.add_argument("--overwrite", action="store_true", help="允许原子覆盖已存在的输出文件")
    args = parser.parse_args(argv)
    if args.min_subset_size < 1 or args.max_condition_depth < 1:
        parser.error("--min-subset-size、--max-condition-depth 必须为正整数")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        stats = run_partition(args.input, args.output, None, DEFAULT_SIMILARITY_THRESHOLD,
                              args.min_subset_size, args.max_condition_depth, args.overwrite,
                              args.metadata_output, args.instances)
    except Exception as error:
        print(f"子集划分失败: {error}", file=sys.stderr)
        return 1
    print(f"\n子集划分完成：实例 {stats['instances']}，caption 根 {stats['caption_roots']}，"
          f"细粒度子集 {stats['subset_nodes']}，输出 {args.output}，metadata {args.metadata_output or args.output.with_name(args.output.stem + '_metadata' + args.output.suffix)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
