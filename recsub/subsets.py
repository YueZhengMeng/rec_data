"""三值分区子集构造（step6 的核心）。

每个节点显式保存 yes / no / unknown 三个分区，避免把 unknown 错当成 no，也避免
把重叠子集误当互斥划分。通过门禁的正谓词可选生成 ``polarity=negative`` 的兄弟
节点，节点带 ``complement_of``。

默认只做深度 1。``max_depth=2`` 时只在同根已通过门禁的谓词之间求交，交集仍须有
足够 yes/no、unknown 足够少、并通过平衡度门禁，组合数因此是可控的。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from recsub.gates import Counts, GateSettings, evaluate, select_with_redundancy


@dataclass(frozen=True, slots=True)
class PredicateDecision:
    predicate_id: str
    display: str
    category: str
    dimension: str
    value: str
    verb: str | None
    polarity: str
    source: str
    yes: frozenset[int]
    no: frozenset[int]
    unknown: frozenset[int]
    parent_ids: frozenset[int]
    raw_phrases: tuple[str, ...] = ()

    @property
    def counts(self) -> Counts:
        return Counts(len(self.parent_ids), len(self.yes), len(self.no), len(self.unknown))

    def to_predicate_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "display": self.display,
            "category": self.category,
            "dimension": self.dimension,
            "value": self.value,
            "verb": self.verb,
            "polarity": self.polarity,
            "source": self.source,
            "raw_phrases": list(self.raw_phrases),
        }


def stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def build_decisions(records: Sequence[Mapping[str, Any]], predicates: Sequence[Mapping[str, Any]],
                    ids_by_display: Mapping[str, list[str]] | None = None) -> list[PredicateDecision]:
    """把逐实例的 ``predicate_id → label`` 读成按谓词聚合的分区。"""
    parent_ids = frozenset(int(record["instance_id"]) for record in records)
    by_id: dict[str, dict[str, set[int]]] = {
        str(predicate["predicate_id"]): {"yes": set(), "no": set(), "unknown": set()}
        for predicate in predicates
    }
    # step5 的标签以 predicate_id 为键，所以不需要候选输出里的显示→id 反向映射。
    for record in records:
        labels = record.get("labels")
        if not isinstance(labels, Mapping):
            continue
        instance_id = int(record["instance_id"])
        for predicate_id, label in labels.items():
            bucket = by_id.get(str(predicate_id))
            if bucket is None or label not in bucket:
                continue
            bucket[label].add(instance_id)

    decisions = []
    for predicate in predicates:
        predicate_id = str(predicate["predicate_id"])
        bucket = by_id[predicate_id]
        # 严格补全：模型漏掉的实例不在 yes/no 里就进入 unknown。
        missing = parent_ids - bucket["yes"] - bucket["no"] - bucket["unknown"]
        bucket["unknown"].update(missing)
        decisions.append(PredicateDecision(
            predicate_id=predicate_id,
            display=str(predicate.get("display") or predicate.get("value") or predicate_id),
            category=str(predicate.get("category") or records[0].get("category") or ""),
            dimension=str(predicate.get("dimension") or ""),
            value=str(predicate.get("value") or ""),
            verb=predicate.get("verb") or None,
            polarity=str(predicate.get("polarity") or "positive"),
            source=str(predicate.get("source") or "reused"),
            yes=frozenset(bucket["yes"]), no=frozenset(bucket["no"]),
            unknown=frozenset(bucket["unknown"]), parent_ids=parent_ids,
            raw_phrases=tuple(predicate.get("raw_phrases") or ()),
        ))
    return decisions


def gate_candidates(decisions: Sequence[PredicateDecision], settings: GateSettings,
                    *, parent_redundant_ids: set[str] | None = None
                    ) -> tuple[list[PredicateDecision], list[dict[str, Any]]]:
    """应用 G1/G2/G3/G5，再按支持集 Jaccard 和数量上限应用 G4/G6。"""
    parent_redundant_ids = parent_redundant_ids or set()
    passed: list[tuple[PredicateDecision, Counts, set[int]]] = []
    rejected: list[dict[str, Any]] = []
    for decision in decisions:
        counts = decision.counts
        ok, failed_gate, numbers = evaluate(
            counts, settings, is_parent_redundant=decision.predicate_id in parent_redundant_ids
        )
        if not ok:
            rejected.append({
                "predicate_id": decision.predicate_id,
                "display": decision.display,
                "gate": failed_gate,
                "counts": numbers,
                "detail": "基础门禁未通过",
            })
            continue
        passed.append((decision, counts, set(decision.yes)))

    selected_tuples, redundancy_rejections = select_with_redundancy(
        [(decision.predicate_id, counts, yes_set)
         for decision, counts, yes_set in passed], settings
    )
    selected_ids = {predicate_id for predicate_id, _, _ in selected_tuples}
    selected = [decision for decision, _, _ in passed if decision.predicate_id in selected_ids]
    rejected.extend(redundancy_rejections)
    return selected, rejected


def node_from_decision(decision: PredicateDecision, *, parent_node_id: str,
                       complement_of: str | None = None,
                       path: Sequence[str] = ()) -> dict[str, Any]:
    """把一个谓词分区写成稳定的可训练/可审核节点。"""
    node_id = stable_id("pred_", parent_node_id, decision.predicate_id, list(path), complement_of)
    payload = {
        "node_id": node_id,
        "node_type": "predicate_subset",
        "parent_ids": [parent_node_id],
        "predicate": decision.to_predicate_dict(),
        "instance_ids": sorted(decision.yes),
        "negative_instance_ids": sorted(decision.no),
        "unknown_instance_ids": sorted(decision.unknown),
        "counts": decision.counts.to_dict(),
        "label_source": "model_pseudo",
        "review": {"status": "pending"},
    }
    if complement_of is not None:
        payload["complement_of"] = complement_of
    return payload


def _gate_payload(counts: Counts, settings: GateSettings) -> dict[str, Any]:
    return {
        "support": counts.yes >= settings.min_support,
        "complement": counts.no >= settings.min_complement,
        "unknown_ratio": round(counts.unknown_ratio, 4),
        "unknown_ok": counts.unknown_ratio <= settings.max_unknown_ratio,
        "balance": round(counts.balance, 4),
        "balance_ok": counts.balance >= settings.min_balance,
        "max_support_jaccard": settings.max_support_jaccard,
    }


def intersect_decisions(left: PredicateDecision, right: PredicateDecision) -> PredicateDecision:
    """同一根的两个条件求交；三值是保守的 Kleene 风格传播：

    - yes = yes ∩ yes
    - no = no(left) ∪ no(right)（任一条件明确不成立，合取就不成立）
    - unknown = 剩余父点
    """
    parent = left.parent_ids & right.parent_ids
    yes = left.yes & right.yes
    no = (left.no | right.no) & parent
    unknown = parent - yes - no
    return PredicateDecision(
        predicate_id=f"{left.predicate_id}__AND__{right.predicate_id}",
        display=f"({left.display}) and ({right.display})",
        category=left.category,
        dimension=f"{left.dimension}+{right.dimension}",
        value=f"{left.value} AND {right.value}",
        verb=None,
        polarity="positive",
        source="intersection",
        yes=frozenset(yes), no=frozenset(no), unknown=frozenset(unknown),
        parent_ids=frozenset(parent),
        raw_phrases=left.raw_phrases + right.raw_phrases,
    )


def build_tree(records: Sequence[Mapping[str, Any]], predicates: Sequence[Mapping[str, Any]],
               parent_node_id: str, settings: GateSettings,
               *, parent_redundant_ids: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """构造一个根的深度 1（可选深度 2）节点与拒绝日志。"""
    decisions = build_decisions(records, predicates)
    selected, rejected = gate_candidates(
        decisions, settings, parent_redundant_ids=parent_redundant_ids
    )
    nodes: list[dict] = []
    for decision in selected:
        node = node_from_decision(decision, parent_node_id=parent_node_id)
        node["gates"] = _gate_payload(decision.counts, settings)
        nodes.append(node)
        if settings.emit_complement:
            complement = PredicateDecision(
                predicate_id=f"{decision.predicate_id}__COMPLEMENT",
                display=f"not {decision.display}", category=decision.category,
                dimension=decision.dimension, value=decision.value, verb=decision.verb,
                polarity="negative", source="complement",
                yes=decision.no, no=decision.yes, unknown=decision.unknown,
                parent_ids=decision.parent_ids, raw_phrases=decision.raw_phrases,
            )
            complement_node = node_from_decision(
                complement, parent_node_id=parent_node_id,
                complement_of=node["node_id"],
            )
            complement_node["gates"] = _gate_payload(complement.counts, settings)
            nodes.append(complement_node)

    if settings.max_depth >= 2 and len(selected) >= 2:
        # 深度 2 只组合同根已通过门禁的谓词；按稳定顺序，且不生成 complement 的 complement。
        for index, left in enumerate(selected):
            for right in selected[index + 1:]:
                intersection = intersect_decisions(left, right)
                ok, failed_gate, numbers = evaluate(intersection.counts, settings)
                if not ok:
                    rejected.append({
                        "predicate_id": intersection.predicate_id,
                        "display": intersection.display,
                        "gate": failed_gate,
                        "counts": numbers,
                        "detail": "深度 2 交集未通过基础门禁",
                    })
                    continue
                node = node_from_decision(
                    intersection, parent_node_id=parent_node_id,
                    path=(left.predicate_id, right.predicate_id),
                )
                node["gates"] = _gate_payload(intersection.counts, settings)
                nodes.append(node)

    return nodes, rejected
