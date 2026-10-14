"""从原始标注派生 (类别, 维度, 取值) 谓词词表。

为什么复用标注词表
------------------
原数据的标注词其实很少：按 (类别, 维度) 统计，person 的 action 43 /
orientation 11 / age 3 / gender 2 撑起绝大部分实例量，car 全部维度合计只有
34 个取值。把它固定下来有两个直接好处：

1. step4 把候选词归一化到这些槽位，而不是每次重新发明字符串；
2. 「新谓词是否带来增量」这个消融，需要一个明确的「仅原词表」基线。

关键设计
--------
- **默认只用 train 划分构建**，避免 val/test 的标注词进入提示词。
- 归一化结果按 ``Slot``（类别, 维度, 取值, 施动者动词, 极性）三元组合并，
  而不是按字符串。因此 ``holding an umbrella`` 与 ``holding a umbrella``
  会合并，而 ``walking to the left``（行人的运动方向）与 ``facing left``
  （静态朝向）不会——它们在原数据里本来就是不同的根。
- ``scope`` 只作为**标记**，不删除条目：``in the first left queue`` 这类
  场景相对取值绑在单张图的布局上，step4 不会把它当作「同类不相似」的补充候选；
  但它仍然保留在词表里，便于统计与人工判断。
- 人工可以用 ``vocabulary_overrides.json`` 改名、合并、删除、加别名，
  不必改代码。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from recsub.io import load_json_any, write_json_atomic

# 原数据 ``type`` 字段的取值集合；表外的归入 other。
DIMENSIONS: tuple[str, ...] = (
    "action", "orientation", "color", "age", "gender",
    "location", "clothing", "accessory", "material", "variety", "size",
)
FALLBACK_DIMENSION = "other"
POLARITIES: tuple[str, ...] = ("positive", "negative")
SCOPES: tuple[str, ...] = ("absolute", "scene_relative")

# ============================================================
# 一、取值规则：把原始标注串拆成 (施动者动词, 取值)
# ============================================================
# 这些规则的作用是让同一意思的不同写法落到同一个 Slot 上。规则刻意保持保守：
# 只处理实测到的、语义无歧义的模式，其余原样保留，交给 embedding 与 LLM 尾部
# 去处理，避免规则误合并。
# 冠词可选：数据里 'wearing a green shirt' 与 'wearing green shirt' 并存，
# 若强制要求冠词，后者抽不出动词，display 会变成 'wearing wearing green shirt'。
_VERB_OBJECT_RE = re.compile(r"^(holding|carrying|wearing)\s+(?:(?:an?|the)\s+)?(.+)$")
_MOTION_DIRECTION_RE = re.compile(
    r"^(walking|running|driving|riding|moving|standing)\s+"
    r"(to the left|to the right|upward|downward|away|"
    r"from left to right|from right to left|towards the camera|away from the camera)$"
)
_FACING_RE = re.compile(r"^facing(?:\s+the)?\s+(forward|backward|left|right)$")
# 原数据同时存在 'right-facing' 与（car 的）'facing forward' 两种写法，统一到
# 「facing + 方向」；front/back 映射成 forward/backward 以便与 car 的写法对齐。
_FACING_SUFFIX_RE = re.compile(r"^(left|right|front|back)-facing$")
_FACING_WORDS = {"front": "forward", "back": "backward"}
# 动词参与槽位身份：'walking on the side' 与 'standing on the side' 在原数据里
# 是两个不同的根，不能合并；而 car 的 'driving on the left carriageway' 与
# 'on the left carriageway' 应当合并，所以动词集合要覆盖实测出现的动作词。
# 同样让冠词可选，并把取值统一成「介词 + the + 地点」，这样
# 'sitting in a chair' 与 'sitting on the chair' 的同类写法不会分裂成两个槽位。
_MOVING_LOCATION_RE = re.compile(
    r"^(driving|walking|riding|standing|sitting|running)\s+(on|in)\s+"
    r"(?:(?:an?|the)\s+)?(.+)$"
)
_ORIENTATION_PHRASE_RE = re.compile(r"^in (horizontal|vertical) orientation$")
# 只在 clothing 维度剥离介词：「in a blue dress」与「wearing a blue dress」同义，
# 但 location 维度的「in the sky」「on the stage」介词承载语义，不能剥。
# 冠词可选：「in blue uniform」与「wearing a blue uniform」必须落到同一槽位。
_CLOTHING_PREPOSITION_RE = re.compile(r"^(?:in|on)\s+(?:(?:an?|the)\s+)?(.+)$")

# 只在两种写法确定同指、且原数据确实并存时才合并，避免我自己发明语义判断。
# 真正的语义合并留给 vocabulary_overrides.json，由人工决定。
_ALIAS_RULES: dict[str, str] = {
    # 'in queue' / 'in a queue' / 'in the queue' 三写法在原数据里并存。
    "in queue": "in the queue",
    "in a queue": "in the queue",
    # 'playing an instrument' 与 'playing a musical instrument' 只差一个形容词。
    "playing an instrument": "playing a musical instrument",
    # 同一动作的动词原形与进行时并存（'walk on the side' / 'walking by on the side'）。
    "walk on the side": "walking on the side",
    "walk by on the side": "walking on the side",
    "walking by on the side": "walking on the side",
    "stand on the side": "standing on the side",
}

# 指向「这张图里第几个/最哪个」的枚举或指代，跨图不可复用。
_ORDINAL = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))"
_SCENE_RELATIVE_RE = re.compile(
    r"\b(?:"
    + _ORDINAL
    + r"|leftmost|rightmost|topmost|bottommost|uppermost|lowermost"
      r"|farthest|furthest|nearest|closest|number\s+\d+|the same"
      r"|(?:left|right|middle|front|back|top|bottom)\s+(?:two|three|four|five|\d+)"
      r"|(?:two|three|four|five|\d+)\s+(?:rows|lines|queues|sections|barriers)"
    # 方位词 + 画面区域名：'on the bottom right side walk' 指的是这张图里的位置。
      r"|(?:top|bottom|upper|lower|left|right|front|back)"
      r"(?:\s+(?:left|right|top|bottom))?\s+"
      r"(?:side\s*walk|sidewalk|area|part|region|portion|corner|edge|band|strip)"
      r")\b",
    re.IGNORECASE,
)


def slug(text: str) -> str:
    """把任意文本压成可用于 id 的短串。"""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def normalize_surface(text: str) -> str:
    """小写、折叠空白、去首尾标点；用于字符串级匹配。"""
    collapsed = re.sub(r"\s+", " ", str(text).strip().lower())
    return collapsed.strip(" .,;:'\"()[]")


def strip_polarity(text: str) -> tuple[str, str]:
    """剥离前导否定词，返回 (剩余文本, 极性)。"""
    stripped = re.sub(r"^(?:not|no)\s+", "", text, flags=re.IGNORECASE).strip()
    return (stripped, "negative") if stripped != text else (text, "positive")


def classify_scope(value: str) -> str:
    """判断取值是绝对的还是绑在这张图布局上的。仅作标记，不删除。"""
    return "scene_relative" if _SCENE_RELATIVE_RE.search(value) else "absolute"


@dataclass(frozen=True, slots=True)
class Slot:
    """一个归一化后的谓词槽位；谓词 id 由它稳定生成。"""

    category: str
    dimension: str
    value: str
    verb: str | None = None
    polarity: str = "positive"

    @property
    def predicate_id(self) -> str:
        segments = [slug(self.category), slug(self.dimension), slug(self.value)]
        if self.verb:
            segments.append(f"verb={slug(self.verb)}")
        if self.polarity == "negative":
            segments.append("polarity=negative")
        return "/".join(segments)

    @property
    def display(self) -> str:
        """还原成数据集的自然写法，用于提示词与可视化。

        否定条目必须把 ``not`` 带上：否则 ``not in the queue`` 与 ``in the queue``
        会给出同一个别名，索引时按字母序 negative 在前，正例词反而被匹配到否定槽位。
        """
        base = f"{self.verb} {self.value}" if self.verb else self.value
        return f"not {base}" if self.polarity == "negative" else base

    def key(self) -> tuple:
        return (self.category, self.dimension, self.value, self.verb, self.polarity)


def _raw_value_rules(dimension: str, value: str) -> tuple[str, str | None, str]:
    """按模式抽取 (维度, 施动者动词, 取值)；无匹配时原样返回。"""
    match = _VERB_OBJECT_RE.match(value)
    if match:
        return dimension, match.group(1), match.group(2)

    match = _MOTION_DIRECTION_RE.match(value)
    if match:
        # 运动方向属于朝向维度：原数据把 walking to the left 记在 orientation 下。
        return "orientation", match.group(1), match.group(2)

    match = _FACING_RE.match(value)
    if match:
        # 静态朝向不提取动词，保证不会与「walking to the left」这类合并。
        return "orientation", None, f"facing {_FACING_WORDS.get(match.group(1), match.group(1))}"

    match = _FACING_SUFFIX_RE.match(value)
    if match:
        direction = match.group(1)
        return "orientation", None, f"facing {_FACING_WORDS.get(direction, direction)}"

    match = _MOVING_LOCATION_RE.match(value)
    if match:
        # 冠词统一成 the：'in a chair' / 'in the chair' 落到同一槽位。
        return "location", match.group(1), f"{match.group(2)} the {match.group(3)}"

    match = _ORIENTATION_PHRASE_RE.match(value)
    if match:
        return "orientation", None, f"{match.group(1)} orientation"

    if dimension == "clothing":
        match = _CLOTHING_PREPOSITION_RE.match(value)
        if match:
            return dimension, None, match.group(1)

    return dimension, None, value


def apply_value_rules(dimension: str, value: str) -> tuple[str, str | None, str]:
    """返回 (维度, 施动者动词, 取值)，并做维度级收尾归一。

    clothing 在原数据里只有「穿戴着」一种语义，写法却有 ``in X`` / ``on X`` /
    ``wearing X`` 三种；只有统一成 ``verb='wearing'`` 它们才会落进同一槽位。
    accessory 不做这件事：``holding a flag`` 与 ``wearing a hat`` 是不同语义，
    动词必须保留在槽位身份里。
    """
    resolved_dimension, verb, normalized = _raw_value_rules(dimension, value)
    if resolved_dimension == "clothing":
        return resolved_dimension, "wearing", normalized
    return resolved_dimension, verb, normalized


def normalize_attribute(category: str, dimension: str | None, attribute: str) -> Slot:
    """把一条原始标注归一化成槽位。"""
    text, polarity = strip_polarity(normalize_surface(attribute))
    text = _ALIAS_RULES.get(text, text)
    resolved_dimension = str(dimension) if dimension in DIMENSIONS else FALLBACK_DIMENSION
    resolved_dimension, verb, value = apply_value_rules(resolved_dimension, text)
    return Slot(slug_category(category), resolved_dimension, value, verb, polarity)


def slug_category(category: str) -> str:
    """类别名保留原始写法，只做小写与去空白；类别名会出现在 id 里做可读来源。"""
    return re.sub(r"\s+", " ", str(category).strip().lower())


# ============================================================
# 二、词表条目
# ============================================================
@dataclass(frozen=True, slots=True)
class Predicate:
    category: str
    dimension: str
    value: str
    verb: str | None = None
    polarity: str = "positive"
    scope: str = "absolute"
    aliases: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    root_count: int = 0
    instance_count: int = 0
    origin: str = "annotation"

    @property
    def predicate_id(self) -> str:
        segments = [slug(self.category), slug(self.dimension), slug(self.value)]
        if self.verb:
            segments.append(f"verb={slug(self.verb)}")
        if self.polarity == "negative":
            segments.append("polarity=negative")
        return "/".join(segments)

    @property
    def slot(self) -> Slot:
        return Slot(self.category, self.dimension, self.value, self.verb, self.polarity)

    @property
    def display(self) -> str:
        return self.slot.display

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "category": self.category,
            "dimension": self.dimension,
            "value": self.value,
            "verb": self.verb,
            "polarity": self.polarity,
            "scope": self.scope,
            "display": self.display,
            "aliases": list(self.aliases),
            "surfaces": list(self.surfaces),
            "root_count": self.root_count,
            "instance_count": self.instance_count,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Predicate":
        return cls(
            category=str(payload["category"]),
            dimension=str(payload["dimension"]),
            value=str(payload["value"]),
            verb=payload.get("verb") or None,
            polarity=str(payload.get("polarity") or "positive"),
            scope=str(payload.get("scope") or "absolute"),
            aliases=tuple(payload.get("aliases") or ()),
            surfaces=tuple(payload.get("surfaces") or ()),
            root_count=int(payload.get("root_count") or 0),
            instance_count=int(payload.get("instance_count") or 0),
            origin=str(payload.get("origin") or "annotation"),
        )


# ============================================================
# 三、词表构建
# ============================================================
def load_split_keys(splits: Mapping[str, Any], split: str) -> set[tuple[str, str]] | None:
    """从 splits.json 取某个划分的 (图片名, caption) 集合；``all`` 返回 None 表示不限。"""
    if split == "all":
        return None
    if split not in splits:
        raise KeyError(f"splits.json 中没有划分 {split!r}，可选：{sorted(splits)}")
    return {(str(image), str(caption)) for image, caption in splits[split]}


def build_vocabulary(
        annotations: Mapping[str, Mapping[str, Mapping[str, Any]]],
        *,
        split_keys: set[tuple[str, str]] | None = None,
        min_root_count: int = 1,
) -> tuple[list[Predicate], dict[str, int]]:
    """统计标注词并归一化成谓词条目。

    ``root_count`` 数的是不同 (图片, caption) 根的数量，而不是出现次数，
    这样「同一个根里出现多次」不会把长尾词顶进词表。
    """
    surfaces: dict[tuple, Counter] = defaultdict(Counter)
    seen_roots: dict[tuple, set[tuple[str, str]]] = defaultdict(set)
    instance_counts: Counter = Counter()
    type_of_dimension: dict[tuple, str] = {}
    raw_root_total = 0
    skipped_by_split = 0

    for image_name, image_annotations in annotations.items():
        for caption, record in image_annotations.items():
            if split_keys is not None and (image_name, caption) not in split_keys:
                skipped_by_split += 1
                continue
            raw_root_total += 1
            attribute = record.get("attribute")
            if attribute is None or not str(attribute).strip():
                continue
            slot = normalize_attribute(record.get("class"), record.get("type"), attribute)
            key = slot.key()
            root = (image_name, caption)
            surfaces[key][str(attribute).strip()] += 1
            seen_roots[key].add(root)
            instance_counts[key] += len(record.get("points") or ())
            type_of_dimension[key] = str(record.get("type"))

    entries: list[Predicate] = []
    for key, root_set in seen_roots.items():
        if len(root_set) < min_root_count:
            continue
        slot = Slot(*key)
        counted = surfaces[key]
        # surfaces 按出现次数排序，只保留少量代表写法便于人工核对。
        top_surfaces = tuple(surface for surface, _ in counted.most_common(3))
        aliases = sorted({
                             normalize_surface(surface) for surface in counted
                         } | {normalize_surface(slot.display)})
        entries.append(Predicate(
            category=slot.category,
            dimension=slot.dimension,
            value=slot.value,
            verb=slot.verb,
            polarity=slot.polarity,
            scope=classify_scope(slot.value),
            aliases=tuple(aliases),
            surfaces=top_surfaces,
            root_count=len(root_set),
            instance_count=instance_counts[key],
        ))

    entries.sort(key=lambda item: (item.category, item.dimension, item.value,
                                   item.verb or "", item.polarity))
    stats = {
        "raw_roots": raw_root_total,
        "skipped_by_split": skipped_by_split,
        "slots_before_min_root_count": len(seen_roots),
        "entries": len(entries),
        "dropped_by_min_root_count": len(seen_roots) - len(entries),
    }
    return entries, stats


# ============================================================
# 四、人工覆盖
# ============================================================
def apply_overrides(entries: Sequence[Predicate],
                    overrides: Mapping[str, Any] | None) -> tuple[list[Predicate], dict[str, int]]:
    """应用 ``vocabulary_overrides.json``：改名 → 合并 → 删别名 → 删除。

    顺序固定，因为合并会消耗掉被合并方，改名会改变 id。
    """
    applied = Counter()
    if not overrides:
        return list(entries), {"relabel": 0, "merge": 0, "drop": 0, "aliases": 0}

    table = {entry.predicate_id: entry for entry in entries}

    for predicate_id, change in (overrides.get("relabel") or {}).items():
        entry = table.get(predicate_id)
        if entry is None:
            continue
        table.pop(predicate_id)
        updated = replace(
            entry,
            dimension=change.get("dimension", entry.dimension),
            value=change.get("value", entry.value),
            verb=change.get("verb", entry.verb),
            polarity=change.get("polarity", entry.polarity),
        )
        table[updated.predicate_id] = updated
        applied["relabel"] += 1

    for source_id, target_id in (overrides.get("merge") or {}).items():
        source, target = table.get(source_id), table.get(target_id)
        if source is None or target is None or source is target:
            continue
        table.pop(source_id)
        table[target_id] = replace(
            target,
            aliases=tuple(sorted(set(target.aliases) | set(source.aliases)
                                 | {normalize_surface(source.display)})),
            surfaces=tuple(dict.fromkeys(target.surfaces + source.surfaces))[:3],
            root_count=target.root_count + source.root_count,
            instance_count=target.instance_count + source.instance_count,
        )
        applied["merge"] += 1

    for predicate_id, extra in (overrides.get("aliases") or {}).items():
        entry = table.get(predicate_id)
        if entry is None:
            continue
        table[predicate_id] = replace(
            entry,
            aliases=tuple(sorted(set(entry.aliases) | {normalize_surface(item) for item in extra})),
        )
        applied["aliases"] += 1

    for predicate_id in (overrides.get("drop") or ()):
        if table.pop(predicate_id, None) is not None:
            applied["drop"] += 1

    result = sorted(table.values(), key=lambda item: (
        item.category, item.dimension, item.value, item.verb or "", item.polarity
    ))
    return result, dict(applied)


# ============================================================
# 五、词表对象
# ============================================================
@dataclass
class Vocabulary:
    entries: list[Predicate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_id_field: dict[str, Predicate] = {}
        self._by_slot: dict[tuple, Predicate] = {}
        self._by_alias: dict[tuple[str, str], Predicate] = {}
        self._by_bare_value: dict[tuple[str, str], Predicate] = {}
        self.reindex()

    def reindex(self) -> None:
        """重建索引。按别名查找按「别名首次出现」优先，保证同义写法指向最早条目。"""
        self._by_id_field = {}
        self._by_slot = {}
        self._by_alias = {}
        self._by_bare_value = {}
        for entry in self.entries:
            self._by_id_field[entry.predicate_id] = entry
            self._by_slot.setdefault(entry.slot.key(), entry)
            for alias in entry.aliases:
                self._by_alias.setdefault((entry.category, alias), entry)
            # 只索引肯定条目：否定与肯定条目的 value 完全相同（'in the queue'），
            # 若也索引否定条目，按字母序 negative 在前会把裸取值匹配到否定槽位。
            if entry.polarity == "positive":
                self._by_bare_value.setdefault(
                    (entry.category, normalize_surface(entry.value)), entry
                )

    # ---- 查询 ----
    def __len__(self) -> int:
        return len(self.entries)

    def predicate_ids(self) -> set[str]:
        return set(self._by_id_field)

    def by_id(self, predicate_id: str) -> Predicate | None:
        return self._by_id_field.get(predicate_id)

    def for_category(self, category: str) -> list[Predicate]:
        key = slug_category(category)
        return [entry for entry in self.entries if entry.category == key]

    def categories(self) -> list[str]:
        return sorted({entry.category for entry in self.entries})

    def dimensions_of(self, category: str) -> dict[str, list[Predicate]]:
        grouped: dict[str, list[Predicate]] = defaultdict(list)
        for entry in self.for_category(category):
            grouped[entry.dimension].append(entry)
        return dict(grouped)

    def match(self, text: str, category: str) -> tuple[str | None, str | None]:
        """把一段候选文本匹配到词表槽位。

        返回 ``(predicate_id, method)``，method ∈ exact / rule / value；
        都匹配不上时返回 ``(None, None)``，交给 embedding 与 LLM 尾部处理。
        """
        key = slug_category(category)
        normalized = normalize_surface(text)
        if not normalized:
            return None, None

        # 1) 完整写法（含否定与动词）直接命中
        entry = self._by_alias.get((key, normalized))
        if entry is not None:
            return entry.predicate_id, "exact"

        # 2) 走一遍规则归一化，落到同一 Slot
        raw_text, polarity = strip_polarity(normalized)
        text_with_alias = _ALIAS_RULES.get(raw_text, raw_text)
        # 维度未知时按 other 试一遍，再按每个已知维度试一遍。
        candidate_dimensions = [FALLBACK_DIMENSION] + list(DIMENSIONS)
        for dimension in dict.fromkeys(candidate_dimensions):
            _, verb, value = apply_value_rules(dimension, text_with_alias)
            slot = Slot(key, dimension, value, verb, polarity)
            entry = self._by_slot.get(slot.key())
            if entry is not None:
                return entry.predicate_id, "rule"

        # 3) 退一步只匹配取值本身（丢掉动词信息，置信度最低）
        entry = self._by_bare_value.get((key, raw_text))
        if entry is not None:
            return entry.predicate_id, "value"
        return None, None

    def parent_slot(self, category: str, dimension: str | None, attribute: str) -> Slot:
        """父 caption 自身的槽位；step4 用它硬过滤与父集同义的候选。"""
        return normalize_attribute(category, dimension, attribute)

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": {**self.metadata, "entry_count": len(self.entries)},
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Vocabulary":
        entries = [Predicate.from_dict(item) for item in payload.get("entries", ())]
        return cls(entries=entries, metadata=dict(payload.get("metadata") or {}))

    def save(self, path: str | Path) -> Path:
        return write_json_atomic(path, self.to_dict())

    @classmethod
    def load(cls, path: str | Path) -> "Vocabulary":
        payload = load_json_any(path)
        if not isinstance(payload, Mapping) or "entries" not in payload:
            raise ValueError(f"{path} 不是词表文件（缺少 entries）")
        return cls.from_dict(payload)

    def add_generated(self, entries: Iterable[Predicate], *,
                      metadata: Mapping[str, Any] | None = None) -> "Vocabulary":
        """追加 step4 生成的新谓词，返回新词表（不改动原对象）。"""
        merged = {entry.predicate_id: entry for entry in self.entries}
        for entry in entries:
            merged.setdefault(entry.predicate_id, entry)
        combined = sorted(merged.values(), key=lambda item: (
            item.category, item.dimension, item.value, item.verb or "", item.polarity
        ))
        return Vocabulary(entries=combined, metadata={**self.metadata, **dict(metadata or {})})

    # ---- 人工浏览 ----
    def to_markdown(self) -> str:
        lines = ["# 标注词表", "",
                 f"- 条目数：{len(self.entries)}",
                 f"- 来源：{self.metadata.get('source', '—')}",
                 f"- 划分：{self.metadata.get('split', '—')}",
                 f"- 构建时间：{self.metadata.get('generated_at', '—')}",
                 "",
                 "`scene_relative` 标记表示该取值绑在这张图的布局上（如"
                 " `in the first left queue`），step4 不会把它当作补充候选，但保留在词表里。",
                 ""]
        for category in self.categories():
            lines.append(f"## {category}")
            lines.append("")
            lines.append("| 维度 | 谓词 id | 写法 | 极性 | 作用域 | 根数 | 实例数 | 出现写法 |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for entry in self.for_category(category):
                lines.append(
                    f"| {entry.dimension} | `{entry.predicate_id}` | {entry.display} "
                    f"| {entry.polarity} | {entry.scope} | {entry.root_count} "
                    f"| {entry.instance_count} "
                    f"| {', '.join(entry.surfaces) or '—'} |"
                )
            lines.append("")
        return "\n".join(lines)
