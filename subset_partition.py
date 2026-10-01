"""基于实例级语义结果，构建 caption 范围内的层次化细粒度子集。

输入数据已经由前置流程完成格式整理和质量检查。本脚本只负责：

1. 在原始 caption 定义的实例集合内收集语义条件；
2. 使用本地 Qwen3-Embedding 对短语做语义归并；
3. 枚举条件交集并确定每个子集的真实实例点集；
4. 将可复现的子集树写入 JSON。

输入格式和质量由前置流程保证；这里只额外提示增强属性与同图其他 caption
的原始属性是否语义重复。输入文件一次性载入内存，然后按图片处理；每张图片
的 Qwen3-Embedding 向量在图片处理完成后释放，不跨图片缓存。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from tqdm import tqdm

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ATTRIBUTE_FIELDS = ("appearance", "action", "location", "relation")
DEFAULT_MODEL_PATH = Path("F:/model/Qwen3-Embedding-0.6B")
DEFAULT_INPUT_PATH = Path("anno/extract_result.json")
DEFAULT_OUTPUT_PATH = Path("anno/fine_grained_subsets.json")

# Qwen3-Embedding 的余弦阈值需要用 REC 属性短语重新校准。
DEFAULT_SIMILARITY_THRESHOLD = 0.86
DEFAULT_MIN_SUBSET_SIZE = 5
DEFAULT_MAX_CONDITION_DEPTH = 3
DEFAULT_BATCH_SIZE = 64
DEFAULT_MAX_LENGTH = 128

_COLOR_TERMS = {
    "black", "white", "red", "green", "blue", "yellow", "orange", "purple",
    "pink", "brown", "gray", "grey", "beige", "tan", "gold", "silver",
    "maroon", "navy", "teal", "cyan", "magenta", "burgundy", "cream",
}
_DIRECTION_OPPOSITES = {
    "horizontal": ({"left"}, {"right"}),
    "vertical": ({"top", "upper", "above", "over"}, {"bottom", "lower", "below", "under"}),
    "depth": ({"front"}, {"behind", "back"}),
    "containment": ({"inside"}, {"outside"}),
}
_POLARITY_TERMS = {"no", "not", "without", "never", "none", "neither", "nor"}


def _exclusive_signature(text: str) -> dict[str, frozenset[str]]:
    """提取不能仅凭高向量相似度跨越的保守语义面。

    Qwen3-Embedding 可能把两个上下文相近、但任务语义相反的短语映射得很近。例如
    ``left``/``right``、不同颜色以及带否定词的表达不能直接合并。
    这里的规则不是替代模型，而是给向量聚类增加一道安全护栏。
    """
    tokens = set(re.findall(r"[\w'-]+", text))
    result = {}
    colors = tokens & _COLOR_TERMS
    colors.discard("grey")
    if colors:
        result["color"] = frozenset(colors)
    for facet, (positive, negative) in _DIRECTION_OPPOSITES.items():
        values = set()
        if tokens & positive:
            values.add("positive")
        if tokens & negative:
            values.add("negative")
        if values:
            result[facet] = frozenset(values)
    result["polarity"] = frozenset({"negative" if tokens & _POLARITY_TERMS else "positive"})
    numbers = frozenset(re.findall(r"\d+(?:\.\d+)?", text))
    if numbers:
        result["number"] = numbers
    return result


def _compatible_phrases(left: str, right: str) -> bool:
    """判断两个短语是否允许进入同一个语义簇。"""
    left_signature = _exclusive_signature(left)
    right_signature = _exclusive_signature(right)
    for facet in left_signature.keys() & right_signature.keys():
        if left_signature[facet].isdisjoint(right_signature[facet]):
            return False
    return True


def _condition_id(field: str, members: tuple[str, ...]) -> str:
    """根据字段和簇内短语生成稳定的条件 ID，便于重复运行对齐结果。"""
    payload = json.dumps([field, members], ensure_ascii=False, separators=(",", ":"), indent=4)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """按 Qwen3-Embedding 官方示例取每条文本的最后一个有效 token。"""
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    import torch
    batch_indices = torch.arange(batch_size, device=last_hidden_states.device)
    return last_hidden_states[batch_indices, sequence_lengths]


class Qwen3EmbeddingEncoder:
    """使用 Qwen3-Embedding 批量编码当前图片中的短语。

    按官方示例使用左 padding、最后有效 token pooling 和 L2 normalization。
    编码结果只在当前图片的处理过程中存在，不跨图片缓存。
    """

    def __init__(self, model_path: str | Path, batch_size: int = DEFAULT_BATCH_SIZE,
                 max_length: int = DEFAULT_MAX_LENGTH, device: str | None = None):
        # 延迟导入，使纯算法单元测试不必立即加载深度学习依赖。
        try:
            import torch
            from transformers import Qwen2TokenizerFast, Qwen3Model
        except ImportError as error:
            raise RuntimeError(
                "缺少 Qwen3-Embedding 依赖；请安装支持 Qwen3Model 的 transformers>=4.51.0 和 PyTorch"
            ) from error
        model_path = Path(model_path)
        if not model_path.is_dir():
            raise FileNotFoundError(f"找不到本地 Qwen3-Embedding 模型目录: {model_path}")
        self.model_path = str(model_path)
        self.torch = torch
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.tokenizer = Qwen2TokenizerFast.from_pretrained(
            str(model_path), padding_side="left", local_files_only=True
        )
        self.model = Qwen3Model.from_pretrained(str(model_path), local_files_only=True)
        self.model.to(self.device)
        self.model.eval()

    def encode(self, phrases: Iterable[str]) -> dict[str, tuple[float, ...]]:
        """仅编码本次传入的唯一短语；批间不保留向量结果。"""
        unique = sorted(set(phrases))
        result = {}
        for start in range(0, len(unique), self.batch_size):
            batch = unique[start:start + self.batch_size]
            tokens = self.tokenizer(batch, padding=True, truncation=True,
                                    max_length=self.max_length, return_tensors="pt")
            tokens = {key: value.to(self.device) for key, value in tokens.items()}
            with self.torch.no_grad():
                hidden = self.model(**tokens).last_hidden_state
                pooled = last_token_pool(hidden, tokens["attention_mask"])
                # 先转 float32 再归一化，避免低精度权重下范数偏离 1。
                pooled = self.torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
            for phrase, vector in zip(batch, pooled.cpu().tolist()):
                result[phrase] = tuple(float(value) for value in vector)
        return result


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """向量已归一化，因此点积就是余弦相似度。"""
    return sum(a * b for a, b in zip(left, right))


def _instance_attribute_entries(record: dict[str, Any]) -> list[dict[str, Any]]:
    """把一个实例的增强属性展平为带原始位置的短语记录。"""
    entries = []
    for field in ATTRIBUTE_FIELDS:
        for index, phrase in enumerate(record["more_attributes"].get(field, [])):
            if phrase:
                entries.append({
                    "field": field,
                    "index": index,
                    "phrase": phrase,
                    "text": phrase,
                    "is_original": False,
                })
    return entries


def deduplicate_instance_attributes(record: dict[str, Any], encoder: Any,
                                    similarity_threshold: float,
                                    vectors: dict[str, tuple[float, ...]] | None = None
                                    ) -> dict[str, list[str]]:
    """全局语义去重实例增强属性，原始 attribute 优先，增强短语保留最短项。

    比较覆盖四个 more_attributes 字段，不限制字段边界；但被选中的增强短语仍保留在它原本所属的字段中。
    若某个增强短语与原始 attributes 语义重复，则从 more_attributes 中删除。
    """
    entries = _instance_attribute_entries(record)
    original = record["attributes"]
    if original:
        entries.append({
            "field": "__original__",
            "index": -1,
            "phrase": original,
            "text": original,
            "is_original": True,
        })
    if not entries:
        return {field: [] for field in ATTRIBUTE_FIELDS}

    if vectors is None:
        vectors = encoder.encode(entry["text"] for entry in entries)
    # 原始标签先进入簇；增强短语按输入文本排序，结果不依赖字段遍历顺序。
    ordered = sorted(entries, key=lambda item: (
        not item["is_original"], item["text"], len(item["phrase"]), item["field"], item["index"]
    ))
    clusters: list[list[dict[str, Any]]] = []
    for entry in ordered:
        compatible_clusters = []
        for cluster_index, cluster in enumerate(clusters):
            if all(_compatible_phrases(entry["text"], member["text"])
                   and _cosine(vectors[entry["text"]], vectors[member["text"]])
                   >= similarity_threshold for member in cluster):
                compatible_clusters.append(cluster_index)
        if compatible_clusters:
            clusters[compatible_clusters[0]].append(entry)
        else:
            clusters.append([entry])

    retained = []
    for cluster in clusters:
        originals = [entry for entry in cluster if entry["is_original"]]
        if originals:
            # 原始属性保留优先级最高，但不用写进增强字段，只过滤重复增强项。
            continue
        retained.append(min(cluster, key=lambda item: (
            len(item["phrase"]), item["text"], item["field"], item["index"]
        )))

    # 按原字段、原始顺序输出剩余增强属性，保持原有 JSON 结构及字段归属。
    retained_keys = {(item["field"], item["index"]) for item in retained}
    return {
        field: [phrase for index, phrase in enumerate(record["more_attributes"].get(field, []))
                if (field, index) in retained_keys]
        for field in ATTRIBUTE_FIELDS
    }


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
    """Select whole, non-overlapping subsets across all captions at each depth."""
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

    selected_masks = [{(1 << len(group)) - 1: tree["root_id"]}
                      for group, tree in zip(groups, trees)]
    for depth in range(0, max_condition_depth + 1):
        # Candidates at the same depth compete as complete point sets. Larger
        # supports win; stable content IDs resolve ties independent of input order.
        ranked = []
        for group_index, paths_by_depth in enumerate(paths_by_group):
            for mask, paths in paths_by_depth.get(depth, {}).items():
                if mask in selected_masks[group_index]:
                    continue  # a shallower layer already represents this point set
                ranked.append((-mask.bit_count(), trees[group_index]["root_id"],
                               tuple(groups[group_index][i]["instance_id"]
                                     for i in range(len(groups[group_index])) if mask & (1 << i)),
                               group_index, mask, paths))
        ranked.sort(key=lambda item: (item[0], item[1], str(item[2]), item[4]))
        taken_points: set[tuple[float, float]] = set()
        taken_instances: set[Any] = set()
        for _, _, _, group_index, mask, paths in ranked:
            group = groups[group_index]
            if mask in selected_masks[group_index]:
                continue
            viable = [path for path in paths if path.parent_mask in selected_masks[group_index]]
            if not viable:
                continue
            path = min(viable, key=lambda candidate: tuple(
                (item.display, item.condition_id) for item in candidate.conditions
            ))
            members = [group[index] for index in range(len(group)) if mask & (1 << index)]
            points = {tuple(item["point"]) for item in members}
            instance_ids = {item["instance_id"] for item in members}
            if points & taken_points or instance_ids & taken_instances:
                continue
            parent_id = selected_masks[group_index][path.parent_mask]
            node = _node_from_path(group, trees[group_index]["root_id"], path, parent_id)
            trees[group_index]["nodes"].append(node)
            selected_masks[group_index][mask] = node["node_id"]
            taken_points.update(points)
            taken_instances.update(instance_ids)
    return trees


def build_subset_tree(records: list[dict[str, Any]], encoder: Any,
                      similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
                      min_subset_size: int = DEFAULT_MIN_SUBSET_SIZE,
                      max_condition_depth: int = DEFAULT_MAX_CONDITION_DEPTH,
                      vectors: dict[str, tuple[float, ...]] | None = None) -> dict[str, Any]:
    """Build one caption's tree (use _select_image_trees for image-wide disjointness)."""
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
            base_key = " ".join([
                group[0]["category"], group[0]["attributes"],
                " ".join(more_attributes),
            ]).strip()
            key = _unique_annotation_key(image_annotations, base_key, node["node_id"])
            image_annotations[key] = _annotation_entry(
                group, node["points"], more_attributes
            )
    return image_annotations


def run_partition(input_path: str | Path, output_path: str | Path, encoder: Any,
                  similarity_threshold: float, min_subset_size: int,
                  max_condition_depth: int, overwrite: bool = False,
                  metadata_output_path: str | Path | None = None) -> dict[str, int]:
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
        # 输入文件约 298 MB，按当前约定一次性载入。
        all_records = json.load(file)
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
                # 同一张图片内的所有短语只编码一次；不把向量缓存到下一张图片。
                image_phrases = {
                    phrase
                    for record in image_records
                    for phrase in [record["attributes"]] + [
                        item for field in ATTRIBUTE_FIELDS
                        for item in record["more_attributes"].get(field, [])
                    ]
                    if phrase
                }
                image_vectors = encoder.encode(image_phrases)
                report_cross_caption_attribute_matches(
                    image_records, encoder, similarity_threshold, image_vectors
                )

                caption_groups = list(group_caption_records(image_records))
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
                "model_path": str(getattr(encoder, "model_path", DEFAULT_MODEL_PATH)),
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
    parser = argparse.ArgumentParser(description="基于本地 Qwen3-Embedding 的 REC-8K 层次化细粒度子集划分")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="实例属性 JSON 数组")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH,
                        help="annotations_format 风格的数据集输出 JSON")
    parser.add_argument("--metadata-output", type=Path, default=None,
                        help="metadata/stats 输出 JSON；默认与数据输出同目录并追加 _metadata")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH, help="本地 Qwen3-Embedding 权重目录")
    parser.add_argument("--similarity-threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD,
                        help="同义短语余弦相似度阈值 [0,1]")
    parser.add_argument("--min-subset-size", type=int, default=DEFAULT_MIN_SUBSET_SIZE,
                        help="保留子集的最少实例数")
    parser.add_argument("--max-condition-depth", type=int, default=DEFAULT_MAX_CONDITION_DEPTH,
                        help="最多新增 more_attributes 的数量（不包含原始 attribute）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Qwen3-Embedding 编码 batch size")
    parser.add_argument("--device", default=None, help="模型设备，例如 cpu 或 cuda")
    parser.add_argument("--overwrite", action="store_true", help="允许原子覆盖已存在的输出文件", default=True)
    args = parser.parse_args(argv)
    if not 0.0 <= args.similarity_threshold <= 1.0:
        parser.error("--similarity-threshold 必须在 [0, 1] 范围内")
    if args.min_subset_size < 1 or args.max_condition_depth < 1 or args.batch_size < 1:
        parser.error("--min-subset-size、--max-condition-depth 和 --batch-size 必须为正整数")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        encoder = Qwen3EmbeddingEncoder(args.model_path, batch_size=args.batch_size, device=args.device)
        stats = run_partition(args.input, args.output, encoder, args.similarity_threshold,
                              args.min_subset_size, args.max_condition_depth, args.overwrite,
                              args.metadata_output)
    except Exception as error:
        print(f"子集划分失败: {error}", file=sys.stderr)
        return 1
    print(f"\n子集划分完成：实例 {stats['instances']}，caption 根 {stats['caption_roots']}，"
          f"细粒度子集 {stats['subset_nodes']}，输出 {args.output}，metadata {args.metadata_output or args.output.with_name(args.output.stem + '_metadata' + args.output.suffix)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
