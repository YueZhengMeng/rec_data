"""语义合并的互斥门禁。

向量相似度单独用会串味，原 ``semantic_attributes.py`` 已经用
``_exclusive_signature`` 挡住「颜色、方向、否定、数值」相反的短语，这里在此
基础上再补三个面，都是在实测里能举出反例的：

- **维度**：``walking to the left``（朝向）与 ``to the left`` 单独看很像，
  但前者是运动方向、后者可能是位置。
- **施动者动词**：``walking to the left``（行人）与 ``driving to the left``
  （车）向量几乎相同，语义对象却不同。
- **关系锚点**：``to the left of a pen`` 与 ``to the left of a car`` 的锚点
  不同，不能合并——合并后跨图标签就没有稳定含义了。

因此合并前先过 :func:`compatible`，只有返回 ``(True, "")`` 才允许按相似度合并。
"""

from __future__ import annotations

import re

from recsub.vocabulary import normalize_surface, strip_polarity

_COLOR_TERMS = {
    "black", "white", "red", "green", "blue", "yellow", "orange", "purple",
    "pink", "brown", "gray", "grey", "beige", "tan", "gold", "silver",
    "maroon", "navy", "teal", "cyan", "magenta", "burgundy", "cream",
}
_DIRECTION_OPPOSITES = {
    "horizontal": ({"left"}, {"right"}),
    "vertical": ({"top", "upper", "above", "over"}, {"bottom", "lower", "below", "under"}),
    "depth": ({"front"}, {"behind", "back"}),
}
# 空间关系词。**这一面是必需的**：``inside a silver blister pack`` 与
# ``near a blister pack`` 的向量相似度很高（共享锚点），但一个是「在里面」、
# 一个是「在旁边」。不区分关系词就会把它们并成同一个谓词，跨图标签随之失去意义。
# 多词标记按短语匹配，单词标记按 token 相等匹配。
_RELATION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("in", ("in", "inside", "within")),
    ("out", ("outside", "out")),
    ("on", ("on", "atop", "upon")),
    ("above", ("above", "over", "on top of")),
    ("below", ("below", "under", "underneath", "beneath")),
    ("beside", ("beside", "next to", "near", "adjacent to", "close to", "alongside", "by")),
    ("left", ("left",)),
    ("right", ("right",)),
    ("behind", ("behind",)),
    ("front", ("in front of", "front")),
    ("edge", ("edge", "border", "rim", "boundary", "margin")),
)
_POLARITY_TERMS = {"no", "not", "without", "never", "none", "neither", "nor"}
# 出现在归一化结果里的施动者动词；参与槽位身份，必须成为互斥面。
_AGENT_VERBS = {
    "walking", "running", "driving", "riding", "moving", "standing",
    "sitting", "holding", "carrying", "wearing", "playing",
}
# 关系短语的锚点：'... of a/the/another <noun>' / '... beside a <noun>'。
_ANCHOR_RE = re.compile(
    r"\b(?:of|beside|next to|near|behind|in front of|above|below)\s+"
    r"(?:a|an|the|another|other|two)\s+([a-z][a-z-]*)"
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w'-]+", text.lower()))


def _relation_facet(text: str) -> frozenset[str]:
    """抽取短语里的空间关系词。

    只比较「两边都出现了关系词」的情况：没有关系词的一边不构成约束，否则
    ``barrel`` 与 ``inside the barrel`` 会被误判为互斥。
    """
    normalized = text.lower()
    tokens = _tokens(normalized)
    found = set()
    for label, markers in _RELATION_MARKERS:
        for marker in markers:
            if " " in marker:
                if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized):
                    found.add(label)
            elif marker in tokens:
                found.add(label)
    return frozenset(found)


def exclusive_signature(text: str, *, dimension: str | None = None,
                        verb: str | None = None) -> dict[str, frozenset[str]]:
    """抽取「一旦不同就不能合并」的语义面。"""
    normalized = normalize_surface(text)
    tokens = _tokens(normalized)
    result: dict[str, frozenset[str]] = {}

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

    # 否定：既看 token，也看显式的 polarity 参数。
    _, stripped_polarity = strip_polarity(normalized)
    negative = bool(tokens & _POLARITY_TERMS) or stripped_polarity == "negative"
    result["polarity"] = frozenset({"negative" if negative else "positive"})

    numbers = frozenset(re.findall(r"\d+(?:\.\d+)?", normalized))
    if numbers:
        result["number"] = numbers

    if dimension:
        result["dimension"] = frozenset({dimension})

    if verb:
        result["verb"] = frozenset({verb})
    else:
        present = tokens & _AGENT_VERBS
        if present:
            result["verb"] = frozenset(present)

    anchor = _ANCHOR_RE.search(normalized)
    if anchor:
        result["anchor"] = frozenset({anchor.group(1)})

    relations = _relation_facet(normalized)
    if relations:
        result["relation"] = relations

    return result


def compatible(left: str, right: str, *, left_dimension: str | None = None,
               right_dimension: str | None = None, left_verb: str | None = None,
               right_verb: str | None = None) -> tuple[bool, str]:
    """两个短语是否允许按相似度合并；第二个返回值是不允许的原因。"""
    left_signature = exclusive_signature(left, dimension=left_dimension, verb=left_verb)
    right_signature = exclusive_signature(right, dimension=right_dimension, verb=right_verb)
    for facet in sorted(left_signature.keys() & right_signature.keys()):
        if left_signature[facet].isdisjoint(right_signature[facet]):
            return False, facet
    return True, ""


def is_refinement(phrase: str, target: str) -> bool:
    """``phrase`` 是否只是 ``target`` 的细化（多了限定词）。

    ``white laces`` 相对词表里的 ``white`` 是细化，不是同义：直接按相似度合并
    会把「鞋带是白的」这个新信息吃掉，最后因为与父属性同义而被丢弃。这类短语
    应当交给 LLM 尾部生成新谓词。
    """
    phrase_tokens = _tokens(normalize_surface(phrase))
    target_tokens = _tokens(normalize_surface(target))
    return bool(target_tokens) and target_tokens < phrase_tokens
