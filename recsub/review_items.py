"""读取 step6 子集节点并构造人工审核条目的点坐标。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.io import iter_jsonl


def _key(node: Mapping[str, Any]) -> tuple[str, str]:
    return str(node["image_name"]), str(node["caption"])


def load_subset_nodes(subsets_path: str | Path) -> list[dict]:
    """读取正向子集，并从同一 caption 根节点解析实例坐标。

    互补节点和 negative 极性节点会被排除，根节点不返回。返回的是脱离原记录的
    副本，调用方可以安全地补充 ``yes_points``/``no_points``/``unknown_points``。
    """
    roots: dict[tuple[str, str], dict[int, Sequence[float]]] = {}
    subsets: list[dict] = []
    for raw in iter_jsonl(subsets_path):
        kind = raw.get("node_type")
        if kind == "caption_root":
            ids, points = raw.get("instance_ids", ()), raw.get("points", ())
            if len(ids) != len(points):
                raise ValueError(f"根节点 {_key(raw)} 的 instance_ids 与 points 数量不一致")
            roots[_key(raw)] = {int(instance_id): point for instance_id, point in zip(ids, points)}
            continue
        if kind != "predicate_subset":
            continue
        predicate = raw.get("predicate") or {}
        if raw.get("complement_of") or predicate.get("polarity") == "negative":
            continue
        subsets.append(dict(raw))

    for node in subsets:
        key = _key(node)
        point_by_id = roots.get(key)
        if point_by_id is None:
            raise ValueError(f"子集 {key} 找不到 caption_root 坐标")
        for field, output in (("instance_ids", "yes_points"),
                              ("negative_instance_ids", "no_points"),
                              ("unknown_instance_ids", "unknown_points")):
            ids = [int(value) for value in node.get(field, ())]
            missing = [instance_id for instance_id in ids if instance_id not in point_by_id]
            if missing:
                raise ValueError(f"子集 {key} 的实例 {missing[:5]} 不在 caption_root 中")
            node[output] = [point_by_id[instance_id] for instance_id in ids]
        yes_ids = {int(value) for value in node.get("instance_ids", ())}
        node["other_instance_ids"] = [i for i in point_by_id if i not in yes_ids]
        node["other_points"] = [point_by_id[i] for i in node["other_instance_ids"]]
    return subsets


def effective_partitions(item: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, list[int]]:
    """Apply explicit yes/no flips without changing unknown instances."""
    yes = set(map(int, item.get("yes_ids", ())))
    no = set(map(int, item.get("no_ids", ())))
    unknown = set(map(int, item.get("unknown_ids", ())))
    for raw_id, label in (state.get("labels") or {}).items():
        instance_id = int(raw_id)
        if instance_id not in yes | no | unknown or label not in {"yes", "no"}:
            continue
        yes.discard(instance_id)
        no.discard(instance_id)
        unknown.discard(instance_id)
        (yes if label == "yes" else no).add(instance_id)
    return {"yes_ids": sorted(yes), "no_ids": sorted(no), "unknown_ids": sorted(unknown)}
