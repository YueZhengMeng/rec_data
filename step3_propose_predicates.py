"""按原 caption 的目标点集整理新增属性候选。

1. 以 (image_name, caption_idx) 收集原始属性和增强属性；
2. 按长度与单词数过滤后，用 Qwen3-Embedding 语义去重；
3. 每个 caption 调用一次文本 LLM，输出该目标集合的候选短语；
4. 保存为 图片名 -> caption 文本 -> 属性短语列表。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

from openai import AsyncOpenAI
from tqdm import tqdm

from config import CONFIG
from llm_api_async import openai_complete_async
from prompts import PREDICATE_PROPOSAL_PROMPT
from semantic_attributes import (
    ATTRIBUTE_FIELDS,
    DEFAULT_MODEL_PATH,
    DEFAULT_SIMILARITY_THRESHOLD,
    Qwen3EmbeddingEncoder,
    deduplicate_phrases,
)
from utils import extract_and_parse_json

DEFAULT_INPUT = Path("anno/extract_result.json")
DEFAULT_OUTPUT = Path("anno/predicate_candidates.json")
DEFAULT_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def load_records(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def group_by_caption(records: list[dict]) -> dict[tuple[str, int], list[dict]]:
    """每个原 caption 的点集是一个独立全集。"""
    groups = defaultdict(list)
    for record in records:
        groups[(record["image_name"], record["caption_idx"])].append(record)
    return groups


def collect_caption_phrases(records: list[dict]) -> list[dict]:
    """收集原始属性和新增属性；原始属性只用于 embedding 去重。"""
    phrases = []
    for record in records:
        if record.get("attributes"):
            phrases.append({
                "phrase": record["attributes"],
                "original": True,
                "category": record["category"],
            })
        for field in ATTRIBUTE_FIELDS:
            for phrase in record.get("more_attributes", {}).get(field, []):
                if phrase:
                    phrases.append({
                        "phrase": phrase,
                        "original": False,
                        "category": record["category"],
                    })
    return phrases


def build_llm_items(phrases: list[dict], encoder, threshold: float,
                    max_length: int, max_words: int = 5) -> list[dict]:
    """先过滤字符数和空格分词数，再按类别去重；只返回新增属性。"""
    filtered = [item for item in phrases
                if len(item["phrase"]) <= max_length
                and len(item["phrase"].split()) <= max_words]
    result = []
    categories = sorted({item["category"] for item in filtered})
    for category in categories:
        category_phrases = [item for item in filtered if item["category"] == category]
        unique = deduplicate_phrases(category_phrases, encoder, threshold)
        result.extend(item for item in unique if not item["original"])
    return result


def build_proposal_messages(category: str, items: list[dict]) -> list[dict]:
    """只发送一个类别和新增词列表，不发送实例/图片等重复信息。"""
    text = (PREDICATE_PROPOSAL_PROMPT + "\nCategory: " + category
            + "\nAdditional attributes: "
            + json.dumps([item["phrase"] for item in items],
                         ensure_ascii=False, separators=(",", ":")))
    return [{"role": "user", "content": text}]


async def propose_one(image_name: str, caption: str, category: str, items: list[dict],
                      originals: set[str], client, model: str) -> list[str] | None:
    """每个 caption 调用一次；输出仅包含该 caption 的新增属性词。"""
    if not items:
        return []
    try:
        messages = await openai_complete_async(
            build_proposal_messages(category, items), client, model
        )
    except Exception as error:
        print(f"❌ {image_name} / {caption} LLM调用异常：{error}，下次运行重试")
        return None
    if not messages:
        print(f"❌ {image_name} / {caption} LLM调用失败，下次运行重试")
        return None
    content = messages[-1].get("content", "")
    selected = extract_and_parse_json(content)
    if not isinstance(selected, list) or any(not isinstance(phrase, str) for phrase in selected):
        print(f"❌ {image_name} / {caption} LLM结果解析失败，下次运行重试")
        return None

    allowed = {item["phrase"] for item in items}
    return list(dict.fromkeys(phrase for phrase in selected
                              if phrase in allowed and phrase.casefold() not in originals))


async def run(args):
    if args.input.resolve() == args.output.resolve():
        raise ValueError("候选输出不能覆盖开放式提取结果")
    records = load_records(args.input)
    groups = group_by_caption(records)
    image_names = list(dict.fromkeys(image_name for image_name, _ in groups))
    if args.max_images > 0:
        image_names = image_names[:args.max_images]
    selected_images = set(image_names)
    selected = [(image_name, caption_idx) for image_name, caption_idx in groups
                if image_name in selected_images]
    caption_keys = set()
    for key in selected:
        text_key = (key[0], groups[key][0]["caption"])
        if text_key in caption_keys:
            raise ValueError(f"同图重复 caption 文本不能作为 JSON 键：{text_key}")
        caption_keys.add(text_key)
    if not selected:
        print("没有需要处理的 caption")
        return

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置 DASHSCOPE_API_KEY")
    print(f"读取 {len(records)} 个实例，处理 {len(selected)} 个 caption / {len(image_names)} 张图片")
    encoder = Qwen3EmbeddingEncoder(
        args.embedding_model, batch_size=args.embedding_batch_size, device=args.device
    )
    client = AsyncOpenAI(api_key=api_key, base_url=args.base_url).with_options(timeout=1800)
    result = load_records(args.output) if args.output.exists() else {}
    if any(not isinstance(captions, dict) for captions in result.values()):
        raise ValueError("已有候选文件使用旧版逐图格式，请换一个输出文件名")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    for start in range(0, len(selected), args.batch_size):
        batch_keys = [key for key in selected[start:start + args.batch_size]
                      if groups[key][0]["caption"] not in result.get(key[0], {})]
        if not batch_keys:
            continue
        items_by_caption = {}
        originals_by_caption = {}
        for key in tqdm(batch_keys, desc="整理 caption 候选"):
            phrases = collect_caption_phrases(groups[key])
            originals_by_caption[key] = {item["phrase"].casefold()
                                         for item in phrases if item["original"]}
            items_by_caption[key] = build_llm_items(
                phrases, encoder, args.similarity_threshold, args.max_length, args.max_words
            )

        tasks = [propose_one(key[0], groups[key][0]["caption"],
                             groups[key][0]["category"], items_by_caption[key],
                             originals_by_caption[key], client, args.model)
                 for key in batch_keys]
        outputs = await asyncio.gather(*tasks)
        for key, candidates in zip(batch_keys, outputs):
            if candidates is None:
                continue
            image_name, _ = key
            caption = groups[key][0]["caption"]
            result.setdefault(image_name, {})[caption] = candidates
            print(f"✅ {image_name} / {caption}：候选属性 {len(candidates)} 个")
        with args.output.open("w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=4)

    print(f"候选属性生成完成：{sum(map(len, result.values()))} 个 caption，结果保存到 {args.output}")


def parse_args():
    parser = argparse.ArgumentParser(description="按原 caption 目标点集生成闭集属性候选")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--embedding-model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--similarity-threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument("--max-length", type=int, default=80)
    parser.add_argument("--max-words", type=int, default=5, help="新增属性短语最多包含的空格分隔单词数")
    parser.add_argument("--max-images", type=int, default=10, help="0表示处理全部图片")
    parser.add_argument("--batch-size", type=int, default=50, help="同时请求的 caption 数")
    parser.add_argument("--embedding-batch-size", type=int, default=64)
    parser.add_argument("--device", default=None)
    parser.add_argument("--model", default=CONFIG["model_name"])
    parser.add_argument("--base-url", default=DEFAULT_API_URL)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
