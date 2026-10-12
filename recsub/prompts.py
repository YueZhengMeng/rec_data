"""三个阶段的提示词：引导式开放提取、归一化尾部改写、闭集三值判断。

与原版的差别
------------
1. **提取不再只靠自由发挥**。末尾附一段来自标注词表的参考词表作风格引导，但
   仍允许自由描述——强制闭集会让模型放弃词表里没有的、真有区分力的特征。
2. **显式禁止重复父属性**。原数据里 253 个「caption－候选词」组合有 55 个是
   「green pen → green barrel」这类与父属性同义的全 yes，提示词要说清楚。
3. **标记样式描述改了**。crop 现在画细轮廓环 + 短引线，提示词里必须说明标记
   是标注而不是物体的一部分，否则模型会把红环当成属性。
4. **判断阶段把「看不见」和「不成立」分开**。只有真的看清了目标的相关部位、
   且明确不同，才允许判 no。
"""

from __future__ import annotations

import json

# ============================================================
# 一、引导式开放提取（step3）
# ============================================================
EXTRACT_ATTRIBUTES_PROMPT = """
You are given three views of the same annotated target instance:
* Image 1 (Global): the full image, for scene context and spatial relations.
* Image 2 (Context): a crop showing the target and its surroundings.
* Image 3 (Fine): a smaller, unmarked crop for local visual details.
Images 1 and 2 mark the target with a thin red outline ring and four short leader lines.
The mark is an annotation, not a property of the object.

The target's category and its known attributes are given at the end. Use them and the
images to identify the marked instance, then describe **additional** attributes of THIS
instance only. Never describe a neighbouring instance, and never describe the scene.

What counts as a useful attribute here: something (a) clearly readable in these images, and
(b) likely to hold for other instances of the same category in other images, so that it can
define a reusable sub-group of the target set. Prefer wording that matches the reference
vocabulary below when one of its entries actually fits; if none fits, describe it in the
same short style — do not force an entry that does not apply.

Rules:
* Do NOT repeat the known attributes, and do NOT paraphrase them. If the known attribute is
  "red", then "red", "reddish", "crimson" and "red barrel" are all forbidden.
* At most <|>Max Per Field<|> phrase(s) per field.
* Omit a field entirely (or use []) when it has no clearly readable, discriminating detail.
  An empty or nearly empty answer is a valid and useful result — do not fill every field.
* Do not describe properties shared by the whole scene (the table, the floor, the
  background, the lighting). Only the target's own properties.
* Do not guess material, intent, or motion from a still image.
* The target may be partly occluded: omit uncertain details rather than hedging.
* For relation, always name the anchor: "to the left of a <visible thing>",
  "below a <visible thing>". A relation without a clear anchor is useless. State whether
  the anchor is a single instance ("the") or any instance of that kind ("a").

Pay particular attention to:
<|>Category Guidance Here<|>

Reference vocabulary for this category (a style guide drawn from this dataset's own
annotations — copy an entry only when it truly describes the marked instance):
<|>Vocabulary Here<|>

Return exactly one JSON object—raw JSON or a single ```json fenced block—and NOTHING ELSE.
The root/top-level output MUST be a single JSON object ({...}), not an array. Use one or more
of these keys only: appearance, action, location, relation. You may omit fields without
clear attributes or use []; each included value must be a list of strings, even if there is
only one item.
Format example:
```json
{
    "appearance": ["..."],
    "action": ["..."],
    "location": ["..."],
    "relation": ["..."]
}
```

Inputs:
Category: <|>Category Here<|>
Known attributes/context: <|>Known Attributes Here<|>
"""

CATEGORY_GUIDANCE = {
    "person": """
* appearance: visible clothing, hair, or accessories.
* action: clear pose or action, such as standing, sitting, or holding an object.
* location: where the person is in the scene, such as by a doorway.
* relation: a clear spatial or physical interaction with another visible instance, such as
  beside a car.
""",
    "car": """
* appearance: visible color, body shape, and which way the car faces, if clear.
* action: a clearly visible state, such as parked, an open door; otherwise leave empty.
* location: where the car is in the scene, such as in a lane or parking space.
* relation: its clear position relative to another visible instance, such as beside another
  car.
""",
}

OTHER_GUIDANCE = """
* appearance: visible color, shape, texture, or clearly identifiable material.
* action: clear pose or action for animals, or an obvious state for other objects; otherwise
  leave empty.
* location: where the target is in the scene, such as on a shelf or inside a box.
* relation: its clear position relative to another visible instance, such as beside a cup.
"""


def build_extract_attributes_prompt(
        category,
        attributes,
        *,
        vocabulary_examples: dict[str, list[str]] | None = None,
        max_per_field: int = 2,
        prompt: str = EXTRACT_ATTRIBUTES_PROMPT,
) -> str:
    """填充类别、已知属性、类别引导和参考词表。

    ``vocabulary_examples`` 由 ``Vocabulary.fewshot`` 生成，**必须已经剔除该实例
    父 caption 自己的槽位及别名**，否则模型会把父属性再写一遍。
    """
    guidance = CATEGORY_GUIDANCE.get(str(category).strip().lower(), OTHER_GUIDANCE).strip()
    if vocabulary_examples:
        blocks = [
            f"* {dimension}: {', '.join(examples)}"
            for dimension, examples in vocabulary_examples.items() if examples
        ]
        vocabulary_text = "\n".join(blocks) if blocks else "(none available)"
    else:
        vocabulary_text = "(none available)"
    return (prompt.replace("<|>Category Here<|>", str(category))
            .replace("<|>Known Attributes Here<|>", str(attributes))
            .replace("<|>Category Guidance Here<|>", guidance)
            .replace("<|>Max Per Field<|>", str(max_per_field))
            .replace("<|>Vocabulary Here<|>", vocabulary_text).strip())


# ============================================================
# 二、归一化尾部改写（step4）
# ============================================================
NORMALIZE_TAIL_PROMPT = """You are given a category, this dataset's controlled attribute
vocabulary for that category, and a list of leftover attribute phrases that could not be
matched to the vocabulary automatically.

For each leftover phrase decide exactly one of:
* {"phrase": "<phrase>", "match": "<predicate_id>"} — it means the same as an existing
  vocabulary entry. Use the entry's id verbatim.
* {"phrase": "<phrase>", "new": {"dimension": "<dimension>", "value": "<value>",
  "verb": "<action verb or null>", "polarity": "positive|negative"}} — it is a genuinely new
  attribute worth reusing across images, expressible in the same style as the vocabulary.
* {"phrase": "<phrase>", "drop": "<short reason>"} — it is too specific to this scene or
  instance, too vague, or not decidable from an image.

Rules:
* Never invent a predicate_id. It must appear verbatim in the vocabulary below.
* Prefer "match" over "new" whenever the phrase is a paraphrase of an entry.
* Prefer "drop" for anything tied to one particular layout ("the third row from the top"),
  for one-off visual noise, and for wording that restates the parent attribute.
* For a new entry, "value" must be short, in English, and reusable: a colour, a shape, a
  material, an action, a direction, or a relation with a named anchor.
* Keep polarity out of "value"; use the "polarity" field.

Return ONLY a JSON list, one object per input phrase, in the same order.

Category: <|>Category Here<|>
Controlled vocabulary (predicate_id → wording):
<|>Vocabulary Here<|>
Leftover phrases (JSON): <|>Phrases Here<|>
"""


def build_normalize_tail_messages(category: str, vocabulary_lines: list[str],
                                  phrases: list[str]) -> list[dict]:
    """组装归一化尾部的纯文本请求（不需要图片）。"""
    text = (NORMALIZE_TAIL_PROMPT
            .replace("<|>Category Here<|>", str(category))
            .replace("<|>Vocabulary Here<|>", "\n".join(vocabulary_lines) or "(empty)")
            .replace("<|>Phrases Here<|>", json.dumps(phrases, ensure_ascii=False)))
    return [{"role": "user", "content": text}]


# ============================================================
# 三、闭集三值判断（step5）
# ============================================================
PREDICATE_JUDGMENT_PROMPT = """You are given three views of the SAME target instance: a
full image, a context crop, and an unmarked fine crop. Images 1 and 2 mark the target with a
thin red outline ring and four short leader lines. The mark is an annotation, not a property
of the object.

Identify ONLY the marked target. Another instance nearby having the attribute is NOT
evidence about the target. Use the full image to resolve relations.

For every supplied attribute decide yes / no / unknown:
* yes — the images show clear evidence the attribute applies to the target.
* no — the images show clear evidence the attribute does NOT apply. Use "no" only when you
  can actually see the relevant part of the target and it clearly differs. Do NOT use "no"
  as a fallback when something is merely hard to see, small, or occluded.
* unknown — the target is small, occluded, or at the image border; the relevant part is not
  visible; or the attribute's meaning or its anchor object is ambiguous.

An attribute that is missing from the original caption is NEVER evidence for "no".
Do not guess. Do not treat the annotation mark as an attribute.

The original caption defines the parent set; assess each attribute only for this target.

Return ONLY JSON: {"labels": {"attribute phrase": "yes/no/unknown", ...}}.
Include every supplied phrase exactly once.

Original caption: <|>Caption Here<|>
Category: <|>Category Here<|>
Attribute phrases (JSON): <|>Phrases Here<|>
"""


def build_judgment_prompt(caption: str, category: str, phrases: list[str],
                          prompt: str = PREDICATE_JUDGMENT_PROMPT) -> str:
    return (prompt.replace("<|>Caption Here<|>", str(caption))
            .replace("<|>Category Here<|>", str(category))
            .replace("<|>Phrases Here<|>", json.dumps(phrases, ensure_ascii=False)))


# ============================================================
# 四、消息组装
# ============================================================
def build_instance_messages(base64_image_global: str, base64_image_context: str,
                            base64_image_fine: str, text_prompt: str) -> list[dict]:
    """保持 Global、Context、Fine、文本的固定顺序，提取与判断共用。

    图片放在文本之前与原实现一致；代价是不同实例之间无法共享提示词前缀缓存
    （每张图的 base64 都不同），换来的行为一致性更重要。
    """
    return [
        {
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{base64_image_global}"}},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{base64_image_context}"}},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{base64_image_fine}"}},
                {"type": "text", "text": text_prompt},
            ],
        }
    ]
