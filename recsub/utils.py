"""图像编码与宽容的 JSON 解析。

解析器依次做四件事：剥离 Markdown 代码块、把 Python 字面量换成 JSON、删除尾随
逗号、补全未闭合的容器，尽量把模型输出救回来。
"""

from __future__ import annotations

import base64
import json
import re
from io import BytesIO

from PIL import Image


def encode_image(image: Image.Image) -> str:
    """统一编码为 JPEG base64。

    源图格式不统一，而在线接口只接收一种 data URL 格式；统一转 JPEG 后，
    在线与本地两条路径的三视图输入尺寸和编码方式保持一致。不传 quality，
    走 Pillow 的默认质量。
    """
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _strip_outer_code_fence(text: str) -> str:
    """Remove one pair of surrounding Markdown code fences, if present."""
    match = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else text.strip()


def _replace_python_literals(text: str) -> str:
    """Replace Python literals outside JSON strings without changing string data."""
    replacements = {"True": "true", "False": "false", "None": "null"}
    result = []
    in_string = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        replacement = None
        for literal, value in replacements.items():
            end = index + len(literal)
            if text[index:end] != literal:
                continue
            previous = text[index - 1] if index else ""
            following = text[end] if end < len(text) else ""
            if (not (previous.isalnum() or previous == "_")
                    and not (following.isalnum() or following == "_")):
                replacement = value
                index = end
                break

        if replacement is not None:
            result.append(replacement)
        else:
            result.append(char)
            index += 1

    return "".join(result)


def _remove_trailing_commas(text: str) -> str:
    """Remove commas before a closing bracket, ignoring commas in strings."""
    result = []
    in_string = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == ",":
            next_index = index + 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
            if next_index < len(text) and text[next_index] in "}]":
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def _close_unfinished_containers(text: str) -> str:
    """Append missing closing brackets when the input ends mid-container."""
    pairs = {"}": "{", "]": "["}
    stack = []
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in pairs and stack and stack[-1] == pairs[char]:
            stack.pop()

    # Do not guess how to close an unterminated string.
    if in_string:
        return text

    return text + "".join("}" if opening == "{" else "]" for opening in reversed(stack))


def repair_json(json_string: str) -> str:
    """尝试以保守方式修复常见的 JSON 格式错误。"""
    if not isinstance(json_string, str):
        return ""

    repaired = _strip_outer_code_fence(json_string.lstrip("﻿"))
    repaired = _replace_python_literals(repaired)
    repaired = _remove_trailing_commas(repaired)
    repaired = _close_unfinished_containers(repaired)
    return repaired.strip()


def extract_and_parse_json(text: str):
    """解析文本中的 JSON；文本包含说明文字时提取其中的对象或数组。"""
    if not isinstance(text, str) or not text.strip():
        return None

    text = text.lstrip("﻿")
    decoder = json.JSONDecoder()

    # 项目中的模型通常直接返回一个 JSON，这也是最可靠、最优先的路径。
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    json_objects = []
    position = 0
    while position < len(text):
        start_positions = [index for index in (
            text.find("{", position), text.find("[", position)
        ) if index >= 0]
        if not start_positions:
            break
        start = min(start_positions)

        try:
            json_object, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            position = start + 1
            continue

        json_objects.append(json_object)
        position = end

    if not json_objects:
        return None

    # 存在多个 JSON 时选择最长对象，避免把模型输出里的短片段当成结果。
    try:
        return max(json_objects, key=lambda value: len(json.dumps(value, ensure_ascii=False)))
    except (TypeError, ValueError):
        return None


def _matches_required_keys(parsed, keys: list[str] | None) -> bool:
    if keys is None:
        return True
    return isinstance(parsed, dict) and all(key in parsed for key in keys)


def match_json(text: str, keys: list[str] | None = None):
    """从模型响应中解析 JSON，并可按顶层键过滤结果。"""
    if not isinstance(text, str) or not text.strip():
        return None

    # 先处理代码块，再处理完整文本；这样多个代码块中前一个无效时，
    # 后一个有效结果仍有机会被解析。
    candidates = [
        block.strip()
        for block in re.findall(
            r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL
        )
    ]
    candidates.append(text.strip())

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        for candidate_text in (candidate, repair_json(candidate)):
            parsed = extract_and_parse_json(candidate_text)
            if parsed is not None and _matches_required_keys(parsed, keys):
                return parsed

    return None
