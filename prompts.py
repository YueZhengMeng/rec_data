# AttributesExtractor 在线推理与 Batch 数据生成共用同一份提示词，
# 避免两个入口分别维护后出现图片含义或输出格式不一致。
EXTRACT_ATTRIBUTES_PROMPT = """
You are given three views of the same annotated target instance:
* Image 1 (Global): the full image, providing scene context and spatial relations. The same semi-transparent orange ring/crosshair marks the annotated target point at a larger scale, which is not part of the scene.
* Image 2 (Context): an crop covering the target and its surroundings. The same marker marks the exact annotated target point at a smaller scale, which is not part of the scene.
* Image 3 (Fine): a smaller crop without a marker, providing local appearance, pose, and action details.

Use all three views together to identify the target whose center corresponds to the marker in Image 2. Produce one fused answer, not separate answers for each image.
The target object's **category** and some **known attributes/context** are provided below. Use them together with the images to identify the correct instance.
Your task is to extract **additional, visually observable attributes** of this specific instance. Do not simply repeat the known attributes.
Pay particular attention to:
* appearance: color, clothing, accessories, shape, material, pattern, etc.
* action/state: walking, sitting, standing, holding something, etc.
* location: top shelf, bottom shelf, near the entrance, inside a box, etc.
* relation: beside a car, next to another object, in the upper-right corner of a box, etc.

Only include attributes that are clearly visible. Do not hallucinate.

You MUST respond with ONLY a single valid JSON object and NOTHING ELSE.
The response must be either raw JSON or a ```json fenced block, and the JSON content MUST be directly parseable by JSON.parse.
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


def build_extract_attributes_prompt(category, attributes, prompt=EXTRACT_ATTRIBUTES_PROMPT):
    """填充类别和已知属性，返回在线/本地推理共用的文本提示词。"""
    return (prompt.replace("<|>Category Here<|>", str(category))
            .replace("<|>Known Attributes Here<|>", str(attributes)).strip())


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
