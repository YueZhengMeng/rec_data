# AttributesExtractor 在线推理与 Batch 数据生成共用同一份提示词，
# 避免两个入口分别维护后出现图片含义或输出格式不一致。
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
    """填充类别、已知属性和简短的类别引导，供在线/本地推理共用。"""
    guidance = CATEGORY_GUIDANCE.get(str(category).strip().lower(), OTHER_GUIDANCE).strip()
    return (prompt.replace("<|>Category Here<|>", str(category))
            .replace("<|>Known Attributes Here<|>", str(attributes))
            .replace("<|>Category Guidance Here<|>", guidance).strip())


def build_extract_attributes_messages(
        base64_image_global,
        base64_image_context,
        base64_image_fine,
        category,
        attributes,
        prompt=EXTRACT_ATTRIBUTES_PROMPT,
):
    """按照 Global、Context、Fine、文本的固定顺序组装多模态消息。"""
    text_prompt = build_extract_attributes_prompt(category, attributes, prompt)
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image_global}"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image_context}"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image_fine}"},
                },
                {
                    "type": "text",
                    "text": text_prompt,
                },
            ],
        }
    ]
