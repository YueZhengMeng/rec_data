"""把开放提取出来的候选短语归一化到词表槽位（原 step3 的替代）。

三级流程，逐级放宽，只有真的判不了才交给 LLM：

1. **规则级**：精确写法 / 别名 / 模式规则（``Vocabulary.match``）。免费且确定。
2. **向量级**：同类别词表条目里做相似度匹配，但必须先过
   :func:`recsub.similarity.compatible` 的互斥门禁（维度、施动者、锚点、颜色、
   方向、否定、数值），并且要求最相似项比次相似项高出 ``margin``。
   只有 ``white laces`` 相对 ``white`` 这种**细化**关系不参与合并——按相似度并进
   去会吃掉「鞋带是白的」这个新信息，最后因为与父属性同义而被丢掉。
3. **LLM 尾部**：按类别批量一次调用，要求逐条给出 ``match`` / ``new`` / ``drop``。
   返回的 ``predicate_id`` 必须原样存在于词表中；``new`` 注册成新条目并标
   ``origin: "generated"``，单独存一份文件，直接支撑「仅原词表 vs 加生成谓词」
   的消融。

最后有一道**硬过滤**：归一化结果等于父 caption 自身槽位的候选一律丢弃。这条由
代码判定、不问 LLM，因为原数据里 253 个「caption－候选词」组合有 55 个是
``green pen → green barrel`` 这类与父属性同义的全 yes。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from recsub.embedding import Vector, cosine
from recsub.vocabulary import (
    DIMENSIONS,
    Predicate,
    Slot,
    Vocabulary,
    classify_scope,
    normalize_surface,
    slug_category,
)

# 合并方式，越靠前越可信。step6 与消融分析会用到这个字段。
METHOD_EXACT = "exact"
METHOD_RULE = "rule"
METHOD_VALUE = "value"
METHOD_EMBEDDING = "embedding"
METHOD_LLM_MATCH = "llm_match"
METHOD_LLM_NEW = "llm_new"

METHOD_ORDER = (METHOD_EXACT, METHOD_RULE, METHOD_VALUE,
                METHOD_EMBEDDING, METHOD_LLM_MATCH, METHOD_LLM_NEW)

# 丢弃理由
DROP_PARENT_REDUNDANT = "parent_redundant"
DROP_LLM = "llm_drop"
DROP_UNRESOLVED = "unresolved"

EXTRACT_FIELDS = ("appearance", "action", "location", "relation")


def extracted_attributes(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """取出模型提取的属性字典，兼容两种历史布局。

    - ``anno/extract_result.json``（28.6 万条历史结果）：``attributes`` 是原始标注
      串（如 ``"red"``），提取结果在 ``more_attributes`` 里；
    - step3 的新格式：提取结果直接在 ``attributes`` 里。
    """
    for field in ("more_attributes", "attributes"):
        value = record.get(field)
        if isinstance(value, Mapping):
            return value
    return None


@dataclass(frozen=True, slots=True)
class PhraseEvidence:
    """一个候选短语及其支持证据。"""

    phrase: str
    field: str  # 来自 extract 的哪个字段（appearance/action/...）
    instance_count: int  # 写出该短语的实例数，是聚合强度的直接信号


@dataclass
class Candidate:
    """归一化后的候选谓词。"""

    predicate_id: str
    category: str
    dimension: str
    value: str
    verb: str | None
    polarity: str
    source: str  # reused（命中词表）| generated（LLM 新造）
    merge_method: str
    raw_phrases: tuple[str, ...]
    instance_count: int
    field: str

    @property
    def display(self) -> str:
        """判断阶段要问的规范写法。

        必须用规范写法而不是各实例的原始短语：同一个谓词在不同实例上问法不一致，
        标签就不可比。否定谓词带上 ``not``，直接对上原数据里 ``not X`` 的根。
        """
        return Slot(self.category, self.dimension, self.value,
                    self.verb, self.polarity).display

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "category": self.category,
            "dimension": self.dimension,
            "value": self.value,
            "verb": self.verb,
            "polarity": self.polarity,
            "display": self.display,
            "source": self.source,
            "merge_method": self.merge_method,
            "raw_phrases": list(self.raw_phrases),
            "instance_count": self.instance_count,
            "field": self.field,
        }


@dataclass
class Dropped:
    phrase: str
    reason: str
    detail: str = ""


@dataclass
class CaptionResult:
    image_name: str
    caption: str
    category: str
    parent_predicate_id: str
    parent_display: str
    candidates: list[Candidate] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    unresolved: list[PhraseEvidence] = field(default_factory=list)


# ============================================================
# 一、按 caption 聚合短语
# ============================================================
def iter_caption_groups(records: Iterable[Mapping[str, Any]]) -> Iterator[tuple[tuple[str, str], str, list[dict]]]:
    """把提取结果按 (image_name, caption) 聚组，边读边产出，内存与文件大小无关。

    step3 按实例顺序追加，所以同一 caption 的记录天然连续。万一把两次不同
    ``--image-names`` 的结果串在一起导致顺序错乱，同一个键会产出**多个**分组，
    并在标准输出提示一次；调用方按 key 累加即可，不会丢数据。
    """
    current_key: tuple[str, str] | None = None
    current_records: list[dict] = []
    closed_keys: set[tuple[str, str]] = set()
    reported = False

    def close():
        """产出一组；用生成器表达式无法内联，这里手动构造。"""
        if current_records and current_key is not None:
            return (current_key, str(current_records[0]["category"]), current_records)
        return None

    for record in records:
        if record.get("failed") or extracted_attributes(record) is None:
            continue
        key = (str(record["image_name"]), str(record["caption"]))
        if key == current_key:
            current_records.append(dict(record))
            continue

        group = close()
        if group is not None:
            closed_keys.add(group[0])
            yield group
        if key in closed_keys and not reported:
            reported = True
            print(f"⚠️ 提取结果未按 caption 连续排列（{key} 再次出现）；"
                  f"该 caption 会被拆成多组，调用方需按 key 累加")
        current_key = key
        current_records = [dict(record)]

    group = close()
    if group is not None:
        yield group


def collect_evidence(records: Sequence[Mapping[str, Any]]) -> list[PhraseEvidence]:
    """统计每个短语被多少个实例写出，并按实例数降序返回。"""
    counters: dict[str, Counter] = {}
    for record in records:
        attributes = extracted_attributes(record)
        if attributes is None:
            continue
        for field_name in EXTRACT_FIELDS:
            for phrase in attributes.get(field_name) or ():
                text = str(phrase).strip()
                if not text:
                    continue
                counters.setdefault(text, Counter())[field_name] += 1
    evidence = [
        PhraseEvidence(phrase, field_counter.most_common(1)[0][0], field_counter.most_common(1)[0][1])
        for phrase, field_counter in counters.items()
    ]
    evidence.sort(key=lambda item: (-item.instance_count, item.phrase))
    return evidence


def merge_evidence(accumulator: dict[str, list], evidence: Sequence[PhraseEvidence]) -> None:
    """把一组证据累加进 ``{短语: [字段, 实例数]}``，用于同一个键被拆成多组时合并。"""
    for item in evidence:
        existing = accumulator.get(item.phrase)
        if existing is None:
            accumulator[item.phrase] = [item.field, item.instance_count]
        else:
            existing[1] += item.instance_count


def accumulator_to_evidence(accumulator: Mapping[str, Sequence]) -> list[PhraseEvidence]:
    evidence = [PhraseEvidence(phrase, str(value[0]), int(value[1]))
                for phrase, value in accumulator.items()]
    evidence.sort(key=lambda item: (-item.instance_count, item.phrase))
    return evidence


# ============================================================
# 二、三级归一化
# ============================================================
@dataclass
class NormalizerSettings:
    similarity_threshold: float = 0.86
    similarity_margin: float = 0.02
    allow_refinement_split: bool = True
    min_tail_instance_count: int = 1
    max_new_predicates_per_category: int = 120


class PredicateNormalizer:
    """把一个 caption 的候选短语归一化成词表谓词。"""

    def __init__(self, vocabulary: Vocabulary, encoder=None,
                 settings: NormalizerSettings | None = None) -> None:
        self.vocabulary = vocabulary
        self.encoder = encoder
        self.settings = settings or NormalizerSettings()
        self._vectors: dict[str, Vector] = {}

    # ---- 向量 ----
    def _vector(self, text: str) -> Vector | None:
        if self.encoder is None:
            return None
        cached = self._vectors.get(text)
        if cached is None:
            cached = self.encoder.encode([text])[text]
            self._vectors[text] = cached
        return cached

    def _embedding_match(self, phrase: str, category: str) -> tuple[Predicate | None, str]:
        """同类别词表里找最相似条目；返回 (条目, 说明)。"""
        if self.encoder is None:
            return None, "未启用向量匹配"
        pool = [entry for entry in self.vocabulary.for_category(category)
                if entry.display and entry.display != phrase]
        if not pool:
            return None, "该类别无可比对条目"
        phrase_vector = self._vector(phrase)
        scored = []
        for entry in pool:
            entry_vector = self._vector(entry.display)
            if entry_vector is None:
                continue
            scored.append((cosine(phrase_vector, entry_vector), entry))
        if not scored:
            return None, "向量缺失"
        scored.sort(key=lambda item: -item[0])
        best_score, best = scored[0]

        from recsub.similarity import compatible, is_refinement

        if best_score < self.settings.similarity_threshold:
            return None, f"最高相似度 {best_score:.3f} 低于阈值"
        if len(scored) > 1 and best_score - scored[1][0] < self.settings.similarity_margin:
            return None, (f"最高与次高仅差 {best_score - scored[1][0]:.3f}，"
                          f"不足以确定（{best.value!r} / {scored[1][1].value!r}）")
        allowed, facet = compatible(
            phrase, best.display,
            right_dimension=best.dimension, right_verb=best.verb,
        )
        if not allowed:
            return None, f"互斥面 {facet} 不兼容"
        if self.settings.allow_refinement_split and is_refinement(phrase, best.display):
            return None, f"{phrase!r} 是 {best.display!r} 的细化，交给 LLM 尾部生成新谓词"
        return best, f"相似度 {best_score:.3f}"

    # ---- 主流程 ----
    def normalize_caption(self, image_name: str, caption: str, category: str,
                          parent: Slot,
                          evidence: Sequence[PhraseEvidence]) -> CaptionResult:
        result = CaptionResult(
            image_name=image_name, caption=caption, category=category,
            parent_predicate_id=parent.predicate_id, parent_display=parent.display,
        )
        # predicate_id → 聚合结果
        matched: dict[str, Candidate] = {}
        unresolved: list[PhraseEvidence] = []

        for item in evidence:
            predicate_id, method = self.vocabulary.match(item.phrase, category)
            entry: Predicate | None = None
            note = ""
            if predicate_id is not None:
                entry = self.vocabulary.by_id(predicate_id)
            else:
                entry, note = self._embedding_match(item.phrase, category)
                method = METHOD_EMBEDDING if entry is not None else None

            if entry is None:
                unresolved.append(item)
                result.dropped.append(Dropped(item.phrase, DROP_UNRESOLVED, note))
                continue

            slot = entry.slot
            if _same_slot(slot, parent):
                result.dropped.append(Dropped(
                    item.phrase, DROP_PARENT_REDUNDANT,
                    f"归一化到父槽位 {parent.predicate_id}",
                ))
                continue

            existing = matched.get(entry.predicate_id)
            if existing is None:
                matched[entry.predicate_id] = Candidate(
                    predicate_id=entry.predicate_id, category=entry.category,
                    dimension=entry.dimension,
                    value=entry.value, verb=entry.verb, polarity=entry.polarity,
                    source="reused", merge_method=method or METHOD_EXACT,
                    raw_phrases=(item.phrase,), instance_count=item.instance_count,
                    field=item.field,
                )
            else:
                existing.raw_phrases = tuple(dict.fromkeys(existing.raw_phrases + (item.phrase,)))
                existing.instance_count += item.instance_count
                # 保留最可信的合并方式。
                if METHOD_ORDER.index(method or METHOD_EXACT) < METHOD_ORDER.index(existing.merge_method):
                    existing.merge_method = method or METHOD_EXACT

        result.candidates = sorted(matched.values(), key=lambda c: -c.instance_count)
        result.unresolved = unresolved
        return result

    # ---- 应用 LLM 尾部结果 ----
    def apply_tail(self, result: CaptionResult, parent: Slot,
                   decisions: Mapping[tuple[str, str], Mapping[str, Any]],
                   new_entries: Mapping[str, Predicate]) -> None:
        """把尾部决策落到一个 caption 的结果上（原地修改）。"""
        # 先清掉 unresolved 的占位丢弃项，避免与尾部结果重复。
        result.dropped = [item for item in result.dropped if item.reason != DROP_UNRESOLVED]
        by_id = {candidate.predicate_id: candidate for candidate in result.candidates}

        for item in result.unresolved:
            decision = decisions.get((result.category, item.phrase))
            if not decision:
                result.dropped.append(Dropped(item.phrase, DROP_UNRESOLVED, "尾部未给出决策"))
                continue

            predicate_id = decision.get("predicate_id")
            if decision.get("kind") == "drop":
                result.dropped.append(Dropped(item.phrase, DROP_LLM,
                                              str(decision.get("reason") or "")))
                continue
            if not predicate_id:
                result.dropped.append(Dropped(item.phrase, DROP_UNRESOLVED, "尾部结果无谓词 id"))
                continue

            is_new = decision.get("kind") == "new"
            entry = new_entries.get(predicate_id) if is_new else self.vocabulary.by_id(predicate_id)
            if entry is None:
                result.dropped.append(Dropped(item.phrase, DROP_UNRESOLVED,
                                              f"尾部返回未知谓词 {predicate_id}"))
                continue

            slot = entry.slot
            if _same_slot(slot, parent):
                result.dropped.append(Dropped(
                    item.phrase, DROP_PARENT_REDUNDANT,
                    f"尾部归一化到父槽位 {parent.predicate_id}",
                ))
                continue

            existing = by_id.get(predicate_id)
            if existing is None:
                candidate = Candidate(
                    predicate_id=predicate_id, category=entry.category,
                    dimension=entry.dimension,
                    value=entry.value, verb=entry.verb, polarity=entry.polarity,
                    source="generated" if is_new else "reused",
                    merge_method=METHOD_LLM_NEW if is_new else METHOD_LLM_MATCH,
                    raw_phrases=(item.phrase,), instance_count=item.instance_count,
                    field=item.field,
                )
                result.candidates.append(candidate)
                by_id[predicate_id] = candidate
            else:
                existing.raw_phrases = tuple(dict.fromkeys(existing.raw_phrases + (item.phrase,)))
                existing.instance_count += item.instance_count
        result.candidates.sort(key=lambda candidate: -candidate.instance_count)
        result.unresolved = []


def _same_slot(left: Slot, right: Slot) -> bool:
    """槽位等价判断；取值按去空白小写比较，容忍大小写与多余空格差异。"""
    return (left.category == right.category
            and left.dimension == right.dimension
            and left.polarity == right.polarity
            and (left.verb or "") == (right.verb or "")
            and normalize_surface(left.value) == normalize_surface(right.value))


# ============================================================
# 三、尾部决策解析
# ============================================================
def build_vocabulary_lines(vocabulary: Vocabulary, category: str) -> list[str]:
    """给 LLM 的词表清单：id → 写法 → 作用域。"""
    return [
        f"{entry.predicate_id} | {entry.display} | {entry.scope}"
        for entry in vocabulary.for_category(category)
    ]


def parse_tail_response(payload: Any, vocabulary: Vocabulary, category: str,
                        requested: Sequence[PhraseEvidence],
                        settings: NormalizerSettings,
                        existing_ids: set[str]) -> tuple[dict[str, dict[str, Any]], list[Predicate], list[str]]:
    """校验尾部输出，返回 (逐短语决策, 新谓词条目, 警告列表)。"""
    decisions: dict[str, dict[str, Any]] = {}
    new_entries: dict[str, Predicate] = {}
    warnings: list[str] = []
    if not isinstance(payload, list):
        return decisions, list(new_entries.values()), ["尾部输出不是 JSON 列表"]

    requested_by_phrase = {item.phrase: item for item in requested}
    category_key = slug_category(category)

    for row in payload:
        if not isinstance(row, Mapping):
            continue
        phrase = str(row.get("phrase") or "").strip()
        evidence = requested_by_phrase.get(phrase)
        if evidence is None:
            continue  # 模型自行加了我们没问的短语，忽略

        if row.get("drop"):
            decisions[phrase] = {"kind": "drop", "reason": str(row["drop"])}
            continue

        match_id = row.get("match")
        if isinstance(match_id, str):
            entry = vocabulary.by_id(match_id.strip())
            if entry is None:
                warnings.append(f"{phrase!r} 匹配到不存在的谓词 {match_id!r}，转 unknown")
                continue
            decisions[phrase] = {"kind": "match", "predicate_id": entry.predicate_id}
            continue

        new_payload = row.get("new")
        if isinstance(new_payload, Mapping):
            dimension = str(new_payload.get("dimension") or "").strip().lower()
            value = str(new_payload.get("value") or "").strip()
            polarity = str(new_payload.get("polarity") or "positive").strip().lower()
            verb = new_payload.get("verb") or None
            if dimension not in DIMENSIONS:
                warnings.append(f"{phrase!r} 的新维度 {dimension!r} 非法，转 unknown")
                continue
            if not value:
                warnings.append(f"{phrase!r} 的新取值是空的，转 unknown")
                continue
            if polarity not in ("positive", "negative"):
                polarity = "positive"
            if verb:
                verb = str(verb).strip().lower() or None
            slot = Slot(category_key, dimension, value, verb, polarity)
            predicate_id = slot.predicate_id
            if predicate_id not in existing_ids and len(new_entries) >= settings.max_new_predicates_per_category:
                warnings.append(f"{phrase!r} 的新谓词超出本类别上限，转 unknown")
                continue
            if predicate_id not in new_entries and predicate_id not in existing_ids:
                new_entries[predicate_id] = Predicate(
                    category=category_key, dimension=dimension, value=value, verb=verb,
                    polarity=polarity, scope=classify_scope(value),
                    aliases=(normalize_surface(slot.display),),
                    surfaces=(phrase,), root_count=0,
                    instance_count=evidence.instance_count, origin="generated",
                )
            elif predicate_id in existing_ids:
                # 与已有条目撞 id：当成匹配处理，不重复注册。
                decisions[phrase] = {"kind": "match", "predicate_id": predicate_id}
                continue
            decisions[phrase] = {"kind": "new", "predicate_id": predicate_id}
            continue

        warnings.append(f"{phrase!r} 的尾部决策无法识别，转 unknown")

    return decisions, list(new_entries.values()), warnings


# ============================================================
# 四、汇总
# ============================================================
def summarize(results: Iterable[CaptionResult]) -> dict[str, Any]:
    """统计合并方式与丢弃理由，用于人工确认归一化是否按预期工作。"""
    methods: Counter = Counter()
    reasons: Counter = Counter()
    sources: Counter = Counter()
    predicate_ids: set[str] = set()
    for result in results:
        for candidate in result.candidates:
            methods[candidate.merge_method] += 1
            sources[candidate.source] += 1
            predicate_ids.add(candidate.predicate_id)
        for dropped in result.dropped:
            reasons[dropped.reason] += 1
    return {
        "merge_methods": dict(methods.most_common()),
        "sources": dict(sources.most_common()),
        "drop_reasons": dict(reasons.most_common()),
        "distinct_predicates": len(predicate_ids),
    }


def group_unresolved(results: Iterable[CaptionResult]) -> dict[str, list[PhraseEvidence]]:
    """按类别汇总未解析短语，并合并同一短语在不同 caption 的实例数。"""
    grouped: dict[str, dict[str, PhraseEvidence]] = defaultdict(dict)
    for result in results:
        bucket = grouped[result.category]
        for item in result.unresolved:
            existing = bucket.get(item.phrase)
            if existing is None:
                bucket[item.phrase] = item
            else:
                bucket[item.phrase] = PhraseEvidence(
                    item.phrase, existing.field, existing.instance_count + item.instance_count
                )
    return {category: sorted(items.values(), key=lambda e: (-e.instance_count, e.phrase))
            for category, items in grouped.items()}
