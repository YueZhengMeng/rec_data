"""把开放提取出来的候选短语归一化到词表槽位（step4 的核心）。

两级流程，逐级放宽：

1. **规则级**：精确写法 / 别名 / 模式规则（``Vocabulary.match``）。免费且确定。
2. **向量级**：在全词表条目中做相似度匹配，并按命中条目的类别标记同类/跨类；
   必须先过 :func:`recsub.similarity.compatible` 的互斥门禁（维度、施动者、锚点、
   颜色、方向、否定、数值），且要求最相似写法比次相似写法高出 ``margin``。
   只有 ``white laces`` 相对 ``white`` 这种**细化**关系不参与合并——按相似度并进
   去会吃掉「鞋带是白的」这个新信息，最后因为与父属性同义而被丢掉。

最后有一道**硬过滤**：归一化结果等于父 caption 自身槽位的候选一律丢弃。这条由
代码判定、不问 LLM：``green pen → green barrel`` 这类候选与父属性同义，若保留会
让判断阶段对「已经写进 caption 的属性」重复提问。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from recsub.embedding import Vector, cosine
from recsub.vocabulary import (
    Predicate,
    Slot,
    Vocabulary,
    normalize_surface,
)

# 合并方式，越靠前越可信；同一谓词被多次命中时保留最可信的一种。
METHOD_EXACT = "exact"
METHOD_RULE = "rule"
METHOD_VALUE = "value"
METHOD_EMBEDDING = "embedding"

METHOD_ORDER = (METHOD_EXACT, METHOD_RULE, METHOD_VALUE, METHOD_EMBEDDING)

# 丢弃理由
DROP_PARENT_REDUNDANT = "parent_redundant"
DROP_UNRESOLVED = "unresolved"
DROP_CROSS_CAPTION_STRING = "cross_caption_string_duplicate"

EXTRACT_FIELDS = ("appearance", "action", "location", "relation")
CANDIDATE_TIERS = ("same_category_similar", "cross_category_similar",
                   "same_category_dissimilar")


def flatten_candidates(entry: Mapping[str, Any]) -> list[dict]:
    """把三类候选展平，并补上判断阶段需要的默认字段。"""
    groups = entry.get("candidates")
    if not isinstance(groups, Mapping):
        raise ValueError("候选记录缺少 candidates；请用当前 step4 重新生成")
    return [{**item, "selection_tier": tier,
             "category": item.get("category") or entry.get("category") or "",
             "verb": item.get("verb"),
             "polarity": item.get("polarity") or "positive"}
            for tier in CANDIDATE_TIERS
            for item in groups.get(tier, ())]


def extracted_attributes(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """取出模型提取的属性字典。

    step3 的记录把提取结果直接放在 ``attributes`` 里；只带 ``instance_id`` 的记录
    把原始标注串放在那里，模型输出放在 ``more_attributes`` 里，因此两者都要看。
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
    instance_count: int  # 写出该短语的不同实例数
    instance_ids: frozenset[int] = frozenset()
    raw_phrases: tuple[str, ...] = ()


@dataclass
class Candidate:
    """归一化后的候选谓词。"""

    predicate_id: str
    category: str
    dimension: str
    value: str
    verb: str | None
    polarity: str
    merge_method: str
    raw_phrases: tuple[str, ...]
    instance_count: int
    selection_tier: str = ""
    supporting_ids: set[int] = field(default_factory=set, repr=False)

    @property
    def display(self) -> str:
        """判断阶段要问的规范写法。

        必须用规范写法而不是各实例的原始短语：同一个谓词在不同实例上问法不一致，
        标签就不可比。否定谓词带上 ``not``，直接对上原数据里 ``not X`` 的根。
        """
        return Slot(self.category, self.dimension, self.value,
                    self.verb, self.polarity).display


@dataclass
class Dropped:
    phrase: str
    reason: str
    detail: str = ""
    predicate_id: str = ""


@dataclass
class CaptionResult:
    image_name: str
    caption: str
    category: str
    parent_predicate_id: str
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
    """同一实例跨字段重复说出一个短语也只算一次支持。"""
    counters: dict[str, Counter] = {}
    supporters: dict[str, set[int]] = defaultdict(set)
    for index, record in enumerate(records):
        attributes = extracted_attributes(record)
        if attributes is None:
            continue
        instance_id = int(record.get("instance_id", -(index + 1)))
        seen: set[str] = set()
        for field_name in EXTRACT_FIELDS:
            for phrase in attributes.get(field_name) or ():
                text = str(phrase).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                counters.setdefault(text, Counter())[field_name] += 1
                supporters[text].add(instance_id)
    evidence = [
        PhraseEvidence(phrase, field_counter.most_common(1)[0][0], len(supporters[phrase]),
                       frozenset(supporters[phrase]))
        for phrase, field_counter in counters.items()
    ]
    evidence.sort(key=lambda item: (-item.instance_count, item.phrase))
    return evidence


def merge_evidence(accumulator: dict[str, list], evidence: Sequence[PhraseEvidence]) -> None:
    """跨非连续分组按 instance_id 并集计数，不重复计算续跑记录。"""
    for item in evidence:
        existing = accumulator.get(item.phrase)
        if existing is None:
            accumulator[item.phrase] = [item.field, set(item.instance_ids),
                                        0 if item.instance_ids else item.instance_count]
        else:
            existing[1].update(item.instance_ids)
            if not item.instance_ids:
                existing[2] += item.instance_count


def accumulator_to_evidence(accumulator: Mapping[str, Sequence]) -> list[PhraseEvidence]:
    evidence = [PhraseEvidence(phrase, str(value[0]), len(value[1]) + int(value[2]),
                               frozenset(value[1]))
                for phrase, value in accumulator.items()]
    evidence.sort(key=lambda item: (-item.instance_count, item.phrase))
    return evidence


# ============================================================
# 二、归一化（规则 → 全词表向量）
# ============================================================
@dataclass
class NormalizerSettings:
    """归一化阈值；默认值集中在 ``recsub/config.py`` 的 ``normalize`` 段。"""

    dedup_similarity_threshold: float
    similarity_threshold: float
    similarity_margin: float
    allow_refinement_split: bool


class PredicateNormalizer:
    """把一个 caption 的候选短语归一化成词表谓词。"""

    def __init__(self, vocabulary: Vocabulary, *,
                 settings: NormalizerSettings, encoder=None) -> None:
        self.vocabulary = vocabulary
        self.encoder = encoder
        self.settings = settings
        self._vocabulary_displays = {entry.display for entry in vocabulary.entries if entry.display}
        self._vocabulary_vectors: dict[str, Vector] = {}

    def prepare_vocabulary_vectors(self) -> None:
        """整张词表仅编码一次；相同写法跨类别共享向量。"""
        if self.encoder is not None and self._vocabulary_displays:
            self._vocabulary_vectors = self.encoder.encode(self._vocabulary_displays)
            # 词表向量由归一化器单独持有，避免再在编码器缓存里保留一份副本。
            if hasattr(self.encoder, "retain_cache"):
                self.encoder.retain_cache(set())

    def encode_image_phrases(self, phrases: Iterable[str], vectors: dict[str, Vector]) -> None:
        """一张图的开放短语（含其他 caption）批量编码，跳过词表与已有向量。"""
        if self.encoder is None:
            return
        missing = {phrase for phrase in phrases if phrase and phrase not in vectors
                   and phrase not in self._vocabulary_vectors}
        if missing:
            vectors.update(self.encoder.encode(missing))

    def release_image_vectors(self, vectors: dict[str, Vector]) -> None:
        """图片结束时释放临时向量；词表向量不受影响。"""
        vectors.clear()
        if self.encoder is not None and hasattr(self.encoder, "retain_cache"):
            self.encoder.retain_cache(set())

    def duplicate_similarity(self, left: str, right: str,
                             vectors: dict[str, Vector]) -> float | None:
        """只用于同图跨 caption 提示；互斥或细化关系不算语义重复。"""
        if self.encoder is None:
            return None
        from recsub.similarity import compatible_signatures, phrase_features

        left_signature, left_tokens = phrase_features(left)
        right_signature, right_tokens = phrase_features(right)
        if (not compatible_signatures(left_signature, right_signature)[0]
                or (right_tokens and right_tokens < left_tokens)
                or (left_tokens and left_tokens < right_tokens)):
            return None
        return cosine(self._vector(left, vectors), self._vector(right, vectors))

    @property
    def vocabulary_vector_count(self) -> int:
        return len(self._vocabulary_vectors)

    # ---- 向量 ----
    def _vector(self, text: str, vectors: dict[str, Vector]) -> Vector | None:
        if self.encoder is None:
            return None
        cached = vectors.get(text)
        if cached is None:
            cached = self._vocabulary_vectors.get(text)
        if cached is None:
            cached = self.encoder.encode([text])[text]
            vectors[text] = cached
        return cached

    def deduplicate_evidence(
            self, evidence: Sequence[PhraseEvidence],
            vectors: dict[str, Vector] | None = None,
    ) -> tuple[list[PhraseEvidence], list[dict[str, Any]]]:
        """根内先去重开放短语，保留最短代表和支持实例的并集。"""
        from recsub.similarity import compatible_signatures, phrase_features

        if vectors is None:
            vectors = {}
        # 字符串去重：忽略大小写、空白及首尾标点。
        exact_groups: dict[str, list[PhraseEvidence]] = defaultdict(list)
        for item in evidence:
            exact_groups[normalize_surface(item.phrase)].append(item)
        ordered = sorted((_merge_phrase_evidence(group) for group in exact_groups.values()),
                         key=lambda item: (len(item.phrase), normalize_surface(item.phrase), item.phrase))

        if self.encoder is not None and len(ordered) > 1:
            # 独立调用仍可根内批量编码；按图调用时命中传入的共享 vectors。
            self.encode_image_phrases((item.phrase for item in ordered), vectors)

        clusters: list[list[PhraseEvidence]] = []
        methods: list[str] = []
        for item in ordered:
            placed = False
            if self.encoder is not None and clusters:
                # 当前短语只解析一次；成员特征仅在这一次比较中使用。
                item_signature, item_tokens = phrase_features(item.phrase)
                for index, cluster in enumerate(clusters):
                    # Complete-link：必须与簇内每条短语都兼容且相似，避免链式误合并。
                    for member in cluster:
                        member_signature, member_tokens = phrase_features(member.phrase)
                        if (not compatible_signatures(item_signature, member_signature)[0]
                                or (member_tokens and member_tokens < item_tokens)
                                or (item_tokens and item_tokens < member_tokens)):
                            break
                        similarity = cosine(self._vector(item.phrase, vectors),
                                            self._vector(member.phrase, vectors))
                        if not similarity >= self.settings.dedup_similarity_threshold:
                            break
                    else:
                        cluster.append(item)
                        methods[index] = "embedding"
                        placed = True
                        break
            if not placed:
                clusters.append([item])
                methods.append("exact" if len(item.raw_phrases) > 1 else "single")

        deduplicated: list[PhraseEvidence] = []
        audit: list[dict[str, Any]] = []
        for cluster, method in zip(clusters, methods):
            merged = _merge_phrase_evidence(cluster)
            deduplicated.append(merged)
            audit.append({"representative": merged.phrase,
                          "raw_phrases": list(merged.raw_phrases),
                          "merge_method": method,
                          "instance_count": merged.instance_count})
        deduplicated.sort(key=lambda item: (-item.instance_count, item.phrase))
        return deduplicated, audit

    def _embedding_match(self, phrase: str, category: str,
                         vectors: dict[str, Vector] | None = None) -> tuple[Predicate | None, str]:
        """先过滤互斥/细化项，再在兼容近邻中判断相似度与 margin。"""
        if self.encoder is None:
            return None, "未启用向量匹配"
        if vectors is None:
            vectors = {}
        from recsub.similarity import compatible_signatures, phrase_features

        # 仅在本次匹配中复用；不增加跨短语/根/图片的特征缓存。
        phrase_signature, phrase_tokens = phrase_features(phrase)
        pool = []
        for entry in self.vocabulary.entries:
            display = entry.display
            if not display:
                continue
            signature, tokens = phrase_features(display, dimension=entry.dimension, verb=entry.verb)
            if not compatible_signatures(phrase_signature, signature)[0]:
                continue
            if self.settings.allow_refinement_split and tokens and tokens < phrase_tokens:
                continue
            pool.append((entry, display))
        if not pool:
            return None, "无语义兼容的词表候选"
        phrase_vector = self._vector(phrase, vectors)
        # 跨类别且同槽位/同写法的条目只占一个相似度名次，否则两次
        # 相同得分会让 margin 永远为零；不同维度的同写法仍保留歧义。
        by_slot = {}
        for entry, display in pool:
            key = (display, entry.dimension, entry.value, entry.verb, entry.polarity)
            order = (entry.category != category, entry.predicate_id)
            previous = by_slot.get(key)
            if previous is None or order < previous[2]:
                by_slot[key] = (entry, display, order)
        scored = []
        for entry, display, order in by_slot.values():
            entry_vector = self._vector(display, vectors)
            if entry_vector is not None:
                scored.append((cosine(phrase_vector, entry_vector), entry, order))
        if not scored:
            return None, "向量缺失"
        scored.sort(key=lambda item: (-item[0], *item[2]))
        best_score, best, _ = scored[0]
        if best_score < self.settings.similarity_threshold:
            return None, f"最高兼容相似度 {best_score:.3f} 低于阈值"
        if len(scored) > 1 and best_score - scored[1][0] < self.settings.similarity_margin:
            return None, (f"最高与次高兼容词仅差 {best_score - scored[1][0]:.3f}，"
                          f"不足以确定（{best.value!r} / {scored[1][1].value!r}）")
        return best, f"相似度 {best_score:.3f}"

    # ---- 主流程 ----
    def normalize_caption(self, image_name: str, caption: str, category: str,
                          parent: Slot, evidence: Sequence[PhraseEvidence],
                          *, vectors: dict[str, Vector] | None = None) -> CaptionResult:
        owns_vectors = vectors is None
        if vectors is None:
            vectors = {}
        try:
            return self._normalize_caption(image_name, caption, category, parent,
                                           evidence, vectors)
        finally:
            if owns_vectors:
                self.release_image_vectors(vectors)

    def _normalize_caption(self, image_name: str, caption: str, category: str,
                           parent: Slot, evidence: Sequence[PhraseEvidence],
                           vectors: dict[str, Vector]) -> CaptionResult:
        result = CaptionResult(
            image_name=image_name, caption=caption, category=category,
            parent_predicate_id=parent.predicate_id,
        )
        deduplicated, _ = self.deduplicate_evidence(evidence, vectors)
        evidence = deduplicated
        # predicate_id → 聚合结果
        matched: dict[str, Candidate] = {}
        unresolved: list[PhraseEvidence] = []

        for item in evidence:
            predicate_id, method = self.vocabulary.match(item.phrase, category)
            entry: Predicate | None = None
            note = ""
            if predicate_id is not None:
                entry = self.vocabulary.by_id(predicate_id)
                if entry is not None and method == METHOD_VALUE:
                    from recsub.similarity import compatible, is_refinement
                    if (not compatible(item.phrase, entry.display,
                                       right_dimension=entry.dimension, right_verb=entry.verb)[0]
                            or is_refinement(item.phrase, entry.display)):
                        entry = None
                        note = "裸取值匹配存在语义歧义"
            else:
                entry, note = self._embedding_match(item.phrase, category, vectors)
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
                    merge_method=method or METHOD_EXACT,
                    raw_phrases=item.raw_phrases or (item.phrase,),
                    instance_count=item.instance_count,
                    supporting_ids=set(item.instance_ids),
                )
            else:
                existing.raw_phrases = tuple(dict.fromkeys(
                    existing.raw_phrases + (item.raw_phrases or (item.phrase,))
                ))
                _merge_candidate_support(existing, item)
                # 保留最可信的合并方式。
                if METHOD_ORDER.index(method or METHOD_EXACT) < METHOD_ORDER.index(existing.merge_method):
                    existing.merge_method = method or METHOD_EXACT

        result.candidates = sorted(matched.values(), key=lambda c: -c.instance_count)
        result.unresolved = unresolved
        return result


def _merge_phrase_evidence(items: Sequence[PhraseEvidence]) -> PhraseEvidence:
    """以最短写法作为代表，同时合并字段、原短语和实例支持集合。"""
    representative = min(items, key=lambda item: (
        len(item.phrase), normalize_surface(item.phrase), item.phrase
    ))
    instance_ids = frozenset(instance_id for item in items for instance_id in item.instance_ids)
    instance_count = (len(instance_ids) if any(item.instance_ids for item in items)
                      else sum(item.instance_count for item in items))
    fields = Counter(item.field for item in items)
    raw_phrases = tuple(dict.fromkeys(
        phrase for item in items for phrase in (item.raw_phrases or (item.phrase,))
    ))
    return PhraseEvidence(representative.phrase, fields.most_common(1)[0][0],
                          instance_count, instance_ids, raw_phrases)


def _merge_candidate_support(candidate: Candidate, item: PhraseEvidence) -> None:
    if item.instance_ids:
        candidate.supporting_ids.update(item.instance_ids)
        candidate.instance_count = len(candidate.supporting_ids)
    else:
        candidate.instance_count += item.instance_count


def _same_slot(left: Slot, right: Slot) -> bool:
    """跨类别复用时也过滤与父根同维度/取值/动词/极性的候选。"""
    return (left.dimension == right.dimension
            and left.polarity == right.polarity
            and (left.verb or "") == (right.verb or "")
            and normalize_surface(left.value) == normalize_surface(right.value))


# ============================================================
# 三、根级判断候选预算
# ============================================================
def assemble_budgeted_candidates(result: CaptionResult, vocabulary: Vocabulary,
                                 parent: Slot, budget: int) -> dict[str, Any]:
    """按同类相似 → 跨类相似 → 同类未提及挑选。"""
    if budget < 0:
        raise ValueError("每根候选预算不能为负")
    original_ids = vocabulary.predicate_ids()
    eligible = [
        candidate for candidate in result.candidates
        if not _same_slot(Slot(candidate.category, candidate.dimension, candidate.value,
                               candidate.verb, candidate.polarity), parent)
    ]
    similar_same = sorted(
        (candidate for candidate in eligible
         if candidate.predicate_id in original_ids
         and candidate.category == result.category),
        key=lambda item: (-item.instance_count, item.predicate_id),
    )
    similar_cross = sorted(
        (candidate for candidate in eligible
         if candidate.predicate_id in original_ids
         and candidate.category != result.category),
        key=lambda item: (-item.instance_count, item.predicate_id),
    )
    already_mentioned = {candidate.predicate_id for candidate in eligible}
    grouped = vocabulary.dimensions_of(result.category)
    pools = {}
    for dimension in sorted(grouped, key=lambda name: (-len(grouped[name]), name)):
        pool = [entry for entry in grouped[dimension]
                if entry.predicate_id not in already_mentioned
                and entry.scope == "absolute" and entry.polarity == "positive"
                and not _same_slot(entry.slot, parent)]
        pool.sort(key=lambda entry: (-entry.root_count, -entry.instance_count,
                                     entry.predicate_id))
        if pool:
            pools[dimension] = pool
    unmentioned: list[Candidate] = []
    for rank in range(max(map(len, pools.values()), default=0)):
        for pool in pools.values():
            if rank >= len(pool):
                continue
            entry = pool[rank]
            unmentioned.append(Candidate(
                predicate_id=entry.predicate_id, category=entry.category,
                dimension=entry.dimension, value=entry.value, verb=entry.verb,
                polarity=entry.polarity, merge_method="unmentioned",
                raw_phrases=(), instance_count=0,
            ))

    tiers = (("same_category_similar", similar_same),
             ("cross_category_similar", similar_cross),
             ("same_category_dissimilar", unmentioned))
    selected: list[Candidate] = []
    seen: set[str] = set()
    audit = {"candidate_budget": budget, "tier_counts": {}, "budget_dropped": []}
    for tier, candidates in tiers:
        selected_count = 0
        for candidate in candidates:
            if candidate.predicate_id in seen:
                continue
            seen.add(candidate.predicate_id)
            if len(selected) < budget:
                candidate.selection_tier = tier
                selected.append(candidate)
                selected_count += 1
            else:
                audit["budget_dropped"].append({
                    "predicate_id": candidate.predicate_id,
                    "tier": tier, "reason": "over_budget",
                })
        audit["tier_counts"][tier] = {"eligible": len(candidates), "selected": selected_count}
    result.candidates = selected
    return audit
