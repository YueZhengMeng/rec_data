"""Gate re-evaluation shared by step8 and the final read-only audit."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG
from recsub.gates import Counts, GateSettings, evaluate, select_with_redundancy

PARTITION_FIELDS = ("instance_ids", "negative_instance_ids", "unknown_instance_ids")


def thresholds(settings: GateSettings) -> dict[str, Any]:
    return {"min_support": settings.min_support, "min_complement": settings.min_complement,
            "min_balance": settings.min_balance, "max_support_jaccard": settings.max_support_jaccard}


def step8_gate_settings(*, min_support: int | None = None, min_complement: int | None = None,
                        min_balance: float | None = None,
                        max_support_jaccard: float | None = None) -> GateSettings:
    gates = CONFIG["gates"]
    return GateSettings(min_support=gates["min_support"] if min_support is None else min_support,
                        min_complement=gates["min_complement"] if min_complement is None else min_complement,
                        max_unknown_ratio=1.0, min_balance=gates["min_balance"] if min_balance is None else min_balance,
                        max_support_jaccard=(gates["max_support_jaccard"] if max_support_jaccard is None
                                             else max_support_jaccard),
                        max_predicates_per_root=gates["max_predicates_per_root"],
                        max_depth=1, emit_complement=False)


def _partition_ids(record: Mapping[str, Any]) -> tuple[set[int], set[int], set[int]]:
    values = []
    for field in PARTITION_FIELDS:
        raw = record.get(field, [])
        if (not isinstance(raw, list) or any(type(value) is not int for value in raw)
                or len(raw) != len(set(raw))):
            raise ValueError(f"{record.get('review_id')} 的 {field} 无效或重复")
        values.append(set(raw))
    if values[0] & values[1] or values[0] & values[2] or values[1] & values[2]:
        raise ValueError(f"{record.get('review_id')} 的三值分区有重叠")
    return tuple(values)  # type: ignore[return-value]


def audit_step8_gates(records: Sequence[Mapping[str, Any]], settings: GateSettings | None = None
                      ) -> dict[str, Any]:
    """Run the same G1/G4/G5 review-time checks as step8, without writing files.

    G2 is skipped exactly as in step8; G3/G6 are inherited from the already selected
    step6 records, while G4 is recomputed within each root.
    """
    settings = settings or step8_gate_settings()
    count_settings = replace(settings, max_unknown_ratio=1.0)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_root: dict[tuple[str, str], list[tuple[str, Counts, set[int], Mapping[str, Any]]]] = defaultdict(list)
    seen_ids: set[str] = set()
    by_predicate: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)

    def reject(record: Mapping[str, Any], gate: str, counts: dict, detail: str) -> None:
        rejected.append({"review_id": record.get("review_id"), "node_id": record.get("node_id"),
                         "image_name": record.get("image_name"), "caption": record.get("caption"),
                         "predicate_id": (record.get("predicate") or {}).get("predicate_id"),
                         "gate": gate, "counts": counts, "thresholds": thresholds(settings),
                         "detail": detail})

    for record in records:
        review_id = record.get("review_id")
        predicate_id = (record.get("predicate") or {}).get("predicate_id")
        if not isinstance(review_id, str) or not review_id or review_id in seen_ids:
            raise ValueError(f"门禁审计中 review_id 缺失或重复：{review_id!r}")
        if not isinstance(predicate_id, str) or not predicate_id:
            raise ValueError(f"门禁审计中 predicate_id 无效：{review_id}")
        root = (record.get("image_name"), record.get("caption"))
        if predicate_id in by_predicate[root]:
            raise ValueError(f"同一 root 下 predicate_id 重复：{root}/{predicate_id}")
        seen_ids.add(review_id)
        by_predicate[root][predicate_id] = record
        yes, no, unknown = _partition_ids(record)
        counts = Counts(len(yes | no | unknown), len(yes), len(no), len(unknown))
        ok, failed_gate, numbers = evaluate(counts, count_settings)
        if not ok:
            reject(record, failed_gate or "unknown", numbers, "人工最终分区未通过 step8 G1/G5 门禁")
        else:
            by_root[root].append((predicate_id, counts, yes, record))

    for root, candidates in sorted(by_root.items()):
        selected, redundancy = select_with_redundancy(
            [(predicate_id, counts, yes) for predicate_id, counts, yes, _ in candidates],
            replace(settings, max_predicates_per_root=len(candidates)))
        for item in redundancy:
            reject(by_predicate[root][item["predicate_id"]], item["gate"], item["counts"], item["detail"])
        accepted_ids = {predicate_id for predicate_id, _, _ in selected}
        for predicate_id, counts, _, record in candidates:
            if predicate_id in accepted_ids:
                accepted.append({"review_id": record.get("review_id"), "image_name": root[0],
                                 "caption": root[1], "predicate_id": predicate_id,
                                 "counts": counts.to_dict(), "gates": {
                        "support": counts.yes >= settings.min_support,
                        "complement": counts.no >= settings.min_complement,
                        "unknown_ratio": round(counts.unknown_ratio, 4),
                        "unknown_gate_skipped": True,
                        "balance": round(counts.balance, 4),
                        "balance_ok": counts.balance >= settings.min_balance,
                    }})
    accepted.sort(key=lambda row: (row["image_name"], row["caption"], row["review_id"]))
    rejected.sort(key=lambda row: (str(row.get("image_name")), str(row.get("caption")), str(row.get("review_id"))))
    return {"settings": thresholds(settings), "input_records": len(records),
            "accepted_records": len(accepted), "rejected_records": len(rejected),
            "accepted": accepted, "rejected": rejected,
            "rejected_by_gate": dict(Counter(row["gate"] for row in rejected))}
