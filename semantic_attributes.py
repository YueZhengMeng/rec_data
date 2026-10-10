"""REC 属性短语的嵌入编码和语义去重，供候选生成与旧版子集脚本复用。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MODEL_PATH = Path("F:/model/Qwen3-Embedding-0.6B")
DEFAULT_SIMILARITY_THRESHOLD = 0.86
DEFAULT_BATCH_SIZE = 64
DEFAULT_MAX_LENGTH = 128
ATTRIBUTE_FIELDS = ("appearance", "action", "location", "relation")

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
    """防止仅凭高向量相似度合并颜色、方向、否定和数值相反的短语。"""
    tokens = set(re.findall(r"[\w'-]+", text.lower()))
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
    left_signature = _exclusive_signature(left)
    right_signature = _exclusive_signature(right)
    for facet in left_signature.keys() & right_signature.keys():
        if left_signature[facet].isdisjoint(right_signature[facet]):
            return False
    return True


def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """按 Qwen3-Embedding 官方示例取最后一个有效 token。"""
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    import torch
    indices = torch.arange(last_hidden_states.shape[0], device=last_hidden_states.device)
    return last_hidden_states[indices, sequence_lengths]


class Qwen3EmbeddingEncoder:
    """批量编码短语；按图片调用 encode，不跨图片缓存向量。"""

    def __init__(self, model_path: str | Path, batch_size: int = DEFAULT_BATCH_SIZE,
                 max_length: int = DEFAULT_MAX_LENGTH, device: str | None = None):
        try:
            import torch
            from transformers import Qwen2TokenizerFast, Qwen3Model
        except ImportError as error:
            raise RuntimeError("需要 Qwen3-Embedding 对应的 PyTorch 与 transformers") from error
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
                pooled = self.torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
            for phrase, vector in zip(batch, pooled.cpu().tolist()):
                result[phrase] = tuple(float(value) for value in vector)
        return result


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _instance_attribute_entries(record: dict[str, Any]) -> list[dict[str, Any]]:
    """旧版子集脚本使用的实例字段和原顺序。"""
    entries = []
    for field in ATTRIBUTE_FIELDS:
        for index, phrase in enumerate(record["more_attributes"].get(field, [])):
            if phrase:
                entries.append({"field": field, "index": index, "phrase": phrase,
                                "text": phrase, "is_original": False})
    return entries


def deduplicate_instance_attributes(record: dict[str, Any], encoder: Any,
                                    similarity_threshold: float,
                                    vectors: dict[str, tuple[float, ...]] | None = None
                                    ) -> dict[str, list[str]]:
    """实例内跨字段归并：原始属性优先，其余保留最短增强短语。"""
    entries = _instance_attribute_entries(record)
    original = record["attributes"]
    if original:
        entries.append({"field": "__original__", "index": -1, "phrase": original,
                        "text": original, "is_original": True})
    if not entries:
        return {field: [] for field in ATTRIBUTE_FIELDS}
    if vectors is None:
        vectors = encoder.encode(entry["text"] for entry in entries)
    ordered = sorted(entries, key=lambda item: (
        not item["is_original"], item["text"], len(item["phrase"]), item["field"], item["index"]
    ))
    clusters: list[list[dict[str, Any]]] = []
    for entry in ordered:
        for cluster in clusters:
            if all(_compatible_phrases(entry["text"], member["text"])
                   and _cosine(vectors[entry["text"]], vectors[member["text"]]) >= similarity_threshold
                   for member in cluster):
                cluster.append(entry)
                break
        else:
            clusters.append([entry])
    retained = []
    for cluster in clusters:
        if not any(entry["is_original"] for entry in cluster):
            retained.append(min(cluster, key=lambda item: (
                len(item["phrase"]), item["text"], item["field"], item["index"]
            )))
    kept = {(item["field"], item["index"]) for item in retained}
    return {field: [phrase for index, phrase in enumerate(record["more_attributes"].get(field, []))
                    if (field, index) in kept] for field in ATTRIBUTE_FIELDS}


def deduplicate_phrases(phrases: list[dict], encoder: Any,
                        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
                        ) -> list[dict]:
    """同一类别的一组短语语义去重；REC 原始标注优先，否则选择最短短语。"""
    by_phrase = {}
    for item in phrases:
        text = item["phrase"]
        if text in by_phrase:
            if item["original"]:
                by_phrase[text] = item
        else:
            by_phrase[text] = item
    if not by_phrase:
        return []
    vectors = encoder.encode(by_phrase)
    ordered = sorted(by_phrase.values(), key=lambda item: (
        not item["original"], len(item["phrase"]), item["phrase"]
    ))
    clusters = []
    for item in ordered:
        for cluster in clusters:
            if all(_compatible_phrases(item["phrase"], other["phrase"])
                   and _cosine(vectors[item["phrase"]], vectors[other["phrase"]])
                   >= similarity_threshold for other in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    return [min(cluster, key=lambda item: (
        not item["original"], len(item["phrase"]), item["phrase"]
    )) for cluster in clusters]

