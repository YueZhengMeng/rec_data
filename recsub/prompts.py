"""两个阶段的提示词：开放提取与闭集三值判断。

几条贯穿全流程的约定
--------------------
1. **提取阶段不带词表**，只给类别与已知属性；词表在 step4 的归一化阶段才介入。
2. **显式禁止复述父属性**。原数据里 253 个「caption－候选词」组合有 55 个是
   「green pen → green barrel」这类与父属性同义的全 yes，提示词要说清楚。
3. **说明标记是标注**。三视图里的红色标记由 crop 画上，不是物体的一部分，
   否则模型会把红环当成属性。
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
* Image 1 (Global): the full image for scene context and spatial relations.
* Image 2 (Context): a crop showing the target and its surroundings.
* Image 3 (Fine): a smaller, unmarked crop for local visual details.
A semi-transparent red marker in Images 1 and 2 points to the target instance; it is not part of the original image.

The target object's **category** and some **known attributes/context** are provided below. Use them together with the images to identify the correct instance.
Your task is to extract **additional, visually observable attributes** of this specific instance. Do not simply repeat the known attributes.
Produce one fused answer, not separate answers for each image. Do not describe a neighboring instance.

Pay particular attention to:
<|>Category Guidance Here<|>

The target may be partially occluded; therefore, only include clearly visible, useful features; omit uncertain details, do not guess material, intent, or motion from a still image, and do not try to fill every field.

Return exactly one JSON object—raw JSON or a single ```json fenced block—and NOTHING ELSE. The root/top-level output MUST be a single JSON object ({...}), not an array. Use one or more of these keys only: appearance, action, location, relation. You may omit fields without clear attributes or use []; each included value must be a list of strings, even if there is only one item.
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
* relation: a clear spatial or physical interaction with another visible instance, such as beside a car.
""",
    "car": """
* appearance: visible color, body shape, and which way the car faces, if clear.
* action: a clearly visible state, such as parked, an open door; otherwise leave empty.
* location: where the car is in the scene, such as in a lane or parking space.
* relation: its clear position relative to another visible instance, such as beside another car.
""",
}

OTHER_GUIDANCE = """
* appearance: visible color, shape, texture, or clearly identifiable material.
* action: clear pose or action for animals, or an obvious state for other objects; otherwise leave empty.
* location: where the target is in the scene, such as on a shelf or inside a box.
* relation: its clear position relative to another visible instance, such as beside a cup.
"""


def build_extract_attributes_prompt(category, attributes, prompt=EXTRACT_ATTRIBUTES_PROMPT):
    """填充类别、已知属性和类别引导，供在线/本地推理共用。"""
    guidance = CATEGORY_GUIDANCE.get(str(category).strip().lower(), OTHER_GUIDANCE).strip()
    return (prompt.replace("<|>Category Here<|>", str(category))
            .replace("<|>Known Attributes Here<|>", str(attributes))
            .replace("<|>Category Guidance Here<|>", guidance).strip())


# ============================================================
# 二、闭集三值判断（step5）
# ============================================================
PREDICATE_JUDGMENT_PROMPT = """
You are given three views of the same annotated target instance:
* Image 1 (Global): the full image for scene context and spatial relations.
* Image 2 (Context): a crop showing the target and its surroundings.
* Image 3 (Fine): a smaller, unmarked crop for local visual details.
A semi-transparent red marker in Images 1 and 2 points to the target instance; it is not part of the original image.

The target object's **category** and some **known attributes/context** are provided below. Use them together with the images to identify the correct instance.
Your task is to **determine if the target instance has the given attributes**.
Produce one fused answer, not separate answers for each image. Do not discriminate a neighboring instance. Do not guess.

For every supplied attribute decide yes / no / unknown:
* yes — the images show clear evidence the attribute applies to the target.
* no — the images show clear evidence the attribute does NOT apply. Use "no" only when you can actually see the relevant part of the target and it clearly differs. Do NOT use "no" as a fallback when something is merely hard to see, small, or occluded.
* unknown — the target is small, occluded, or at the image border; the relevant part is not visible; or the attribute's meaning or its anchor object is ambiguous.

Return exactly one JSON object—raw JSON or a single ```json fenced block—and NOTHING ELSE. The root/top-level output MUST be a single JSON object ({...}), not an array. Use one or more of these keys only: yes, no, unknown. You may omit fields without clear attributes or use []; each included value must be a list of strings, even if there is only one item.
Format example:
```json
{
    "yes": ["..."],
    "no": ["..."],
    "unknown": ["..."]
}
```

Inputs:
Category: <|>Category Here<|>
Known attributes/context: <|>Known Attributes Here<|>
Attribute to be judged (JSON):
<|>Phrases Here<|>
"""


def build_judgment_prompt(attributes: str, category: str, phrases: list[str],
                          prompt: str = PREDICATE_JUDGMENT_PROMPT) -> str:
    return (prompt.replace("<|>Known Attributes Here<|>", str(attributes))
            .replace("<|>Category Here<|>", str(category))
            .replace("<|>Phrases Here<|>", json.dumps(phrases, ensure_ascii=False)))


# ============================================================
# 三、消息组装
# ============================================================
def build_instance_messages(base64_image_global: str, base64_image_context: str,
                            base64_image_fine: str, text_prompt: str) -> list[dict]:
    """保持 Global、Context、Fine、文本的固定顺序，提取与判断共用。

    图片放在文本之前；代价是不同实例之间无法共享提示词前缀缓存（每张图的
    base64 都不同），换来的行为一致性更重要。
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
