"""子集质量门禁。

五条可解释的门禁逐条给出实际数值，没通过的也记进决策日志——「哪些条件被自动
丢弃、为什么」本身就是论文里可划分性分析的素材。

默认值集中在 ``recsub/config.py`` 的 ``gates`` 段，:class:`GateSettings` 只做
字段校验，不自带第二套默认值。

| 门禁 | 默认 | 作用 |
|---|---|---|
| G1 支持 | ``min_support=2`` 且 ``min_complement=2`` | 两侧至少 2 个实例，避免单实例侧 |
| G2 unknown | ``max_unknown_ratio=0.90`` | unknown 占父集比例超过 90% 时拒绝 |
| G3 父冗余 | 硬性 | step4 已过滤，这里复验 |
| G4 子集互冗余 | ``max_support_jaccard=0.80`` | 砍掉支持集近似相同的条件 |
| G5 区分度 | ``min_balance=0.10`` | ``min(yes,no)/n`` 下限，过滤过度不均衡的划分 |
| G6 数量上限 | ``max_predicates_per_root=3`` | 同一根最多接受的谓词数 |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GATE_SUPPORT = "G1_support"
GATE_UNKNOWN = "G2_unknown_ratio"
GATE_PARENT = "G3_parent_redundant"
GATE_REDUNDANT = "G4_subset_redundancy"
GATE_BALANCE = "G5_balance"
GATE_CAP = "G6_over_cap"


@dataclass(frozen=True)
class GateSettings:
    min_support: int
    min_complement: int
    max_unknown_ratio: float
    min_balance: float
    max_support_jaccard: float
    max_predicates_per_root: int
    max_depth: int
    emit_complement: bool

    def __post_init__(self) -> None:
        if self.min_support < 1 or self.min_complement < 0:
            raise ValueError("min_support 必须 ≥1，min_complement 不能为负")
        if not 0.0 <= self.max_unknown_ratio <= 1.0:
            raise ValueError("max_unknown_ratio 必须在 [0, 1]")
        if not 0.0 <= self.min_balance <= 0.5:
            raise ValueError("min_balance 必须在 [0, 0.5]")
        if not 0.0 <= self.max_support_jaccard <= 1.0:
            raise ValueError("max_support_jaccard 必须在 [0, 1]")
        if self.max_predicates_per_root < 1:
            raise ValueError("max_predicates_per_root 必须 ≥1")
        if self.max_depth < 1:
            raise ValueError("max_depth 必须 ≥1")


@dataclass
class Counts:
    parent: int
    yes: int
    no: int
    unknown: int

    @property
    def judged(self) -> int:
        return self.yes + self.no + self.unknown

    @property
    def balance(self) -> float:
        """``min(yes, no) / parent``：越接近 0.5 越有区分力。"""
        return 0.0 if not self.parent else min(self.yes, self.no) / self.parent

    @property
    def unknown_ratio(self) -> float:
        return 0.0 if not self.parent else self.unknown / self.parent

    def to_dict(self) -> dict[str, int]:
        return {"parent": self.parent, "yes": self.yes, "no": self.no,
                "unknown": self.unknown}


def jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def evaluate(counts: Counts, settings: GateSettings, *, is_parent_redundant: bool = False
             ) -> tuple[bool, str | None, dict[str, Any]]:
    """逐条过门禁；返回 (是否通过, 失败门禁名, 实际数值)。

    数值一律返回，便于决策日志里能看出「差多少被砍掉」。
    """
    numbers: dict[str, Any] = {
        "support": counts.yes,
        "complement": counts.no,
        "unknown": counts.unknown,
        "parent": counts.parent,
        "balance": round(counts.balance, 4),
        "unknown_ratio": round(counts.unknown_ratio, 4),
    }
    if is_parent_redundant:
        return False, GATE_PARENT, numbers
    if counts.yes < settings.min_support or counts.no < settings.min_complement:
        return False, GATE_SUPPORT, numbers
    if counts.unknown_ratio > settings.max_unknown_ratio:
        return False, GATE_UNKNOWN, numbers
    if counts.balance < settings.min_balance:
        return False, GATE_BALANCE, numbers
    return True, None, numbers


def rank_key(counts: Counts, predicate_id: str) -> tuple:
    """越有区分力的条件排越前：先看平衡度，再看 unknown 少，最后按 id 稳定排序。"""
    return (-counts.balance, counts.unknown_ratio, predicate_id)


def select_with_redundancy(candidates: list[tuple[str, Counts, set]], settings: GateSettings
                           ) -> tuple[list[tuple[str, Counts, set]], list[dict[str, Any]]]:
    """按区分力排序后依次接受，并施加 G4 互冗余与数量上限。

    ``candidates`` 是 ``(predicate_id, Counts, yes_set)``。返回 (接受列表, 拒绝记录)。
    先排序再判冗余，可以让「更有区分力的那个」在近似同集的一对里胜出。
    """
    ordered = sorted(candidates, key=lambda item: rank_key(item[1], item[0]))
    accepted: list[tuple[str, Counts, set]] = []
    rejected: list[dict[str, Any]] = []
    for predicate_id, counts, yes_set in ordered:
        if len(accepted) >= settings.max_predicates_per_root:
            rejected.append({"predicate_id": predicate_id, "gate": GATE_CAP,
                             "counts": counts.to_dict(),
                             "detail": f"该根已接受 {len(accepted)} 个条件（上限 "
                                       f"{settings.max_predicates_per_root}）"})
            continue
        worst_id, worst = None, 0.0
        for other_id, _, other_set in accepted:
            score = jaccard(yes_set, other_set)
            if score > worst:
                worst_id, worst = other_id, score
        if worst > settings.max_support_jaccard:
            rejected.append({"predicate_id": predicate_id, "gate": GATE_REDUNDANT,
                             "counts": counts.to_dict(),
                             "detail": f"与已接受的 {worst_id} 支持集 Jaccard "
                                       f"{worst:.3f} > {settings.max_support_jaccard}"})
            continue
        accepted.append((predicate_id, counts, yes_set))
    return accepted, rejected
