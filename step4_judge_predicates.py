"""按原 caption 的候选属性逐实例三值判断，只保存判断结果。

输入：图片 -> caption -> 新增属性列表 JSON。
输出：每个实例的 yes / no / unknown 三个列表；子集划分交给 subset_partition.py。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from agents import PredicateJudge
from config import CONFIG
from crop import AdaptiveCropper, CropConfig
from utils import match_json

DEFAULT_CANDIDATES = Path("anno/predicate_candidates.json")
DEFAULT_INSTANCES = Path("anno/instance_data.json")
DEFAULT_OUTPUT = Path("anno/predicate_labels.json")
DEFAULT_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def load_json(path: Path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data):
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def caption_contains_phrase(caption: str, phrase: str) -> bool:
    """仅过滤完全重复的属性；其他语义相似性在候选阶段由 LLM 判断。"""
    return phrase.lower() in caption.lower()


def phrases_for_instance(candidates: dict, instance: dict) -> list[str]:
    """取当前 caption 的候选；同图跨 caption 重复只打印，不修改候选。"""
    image_name = instance["image_name"]
    caption = instance["caption"]
    image_candidates = candidates[image_name]
    phrases = []
    for phrase in image_candidates.get(caption, []):
        if caption_contains_phrase(caption, phrase):
            continue
        for other_caption, other_phrases in image_candidates.items():
            if other_caption == caption:
                continue
            # 同一个词出现在其他 caption 的候选，或已在其他 caption 文本里。
            repeated_candidate = any(phrase.casefold() == other.casefold() for other in other_phrases)
            repeated_caption = caption_contains_phrase(other_caption, phrase)
            if repeated_candidate or repeated_caption:
                print(f"⚠️ 跨caption属性重复：{image_name} / {caption}，"
                      f"新增属性 {phrase!r} 与 {other_caption!r} 重复")
        phrases.append(phrase)
    return phrases


def build_work_list(instances: list[dict], candidates: dict, completed: set[int],
                    max_images: int) -> list[tuple[dict, list[str]]]:
    """把所有候选 caption 的实例摊平成一个待判断列表。

    按实例而不是按图片组织，实例少的图片也能凑满一个 batch，避免并发浪费。
    """
    image_names = list(candidates)
    if max_images > 0:
        image_names = image_names[:max_images]
    selected = set(image_names)
    work = []
    for instance in instances:
        if instance["instance_id"] in completed:
            continue
        if instance["image_name"] not in selected:
            continue
        # 只判断有候选的 caption；其余 caption 不在本次候选范围内。
        if instance["caption"] not in candidates[instance["image_name"]]:
            continue
        work.append((instance, phrases_for_instance(candidates, instance)))
    return work


def parse_labels(content: str, phrases: list[str]) -> dict[str, list[str]]:
    """将模型的逐词判断整理成三个列表；缺项和无效值进入 unknown。"""
    parsed = match_json(content)
    labels = parsed.get("labels", {}) if isinstance(parsed, dict) else {}
    if not isinstance(labels, dict):
        labels = {}
    result = {"yes": [], "no": [], "unknown": []}
    for phrase in dict.fromkeys(phrases):
        label = labels.get(phrase)
        if not isinstance(label, str) or label not in result:
            label = "unknown"
        result[label].append(phrase)
    return result


async def judge_one(judge: PredicateJudge, instance: dict, phrases: list[str]) -> dict:
    """复用原开放式提取的全图、context、fine 三视图；只换文本提示。"""
    try:
        if phrases:
            content, messages = await judge.judge(instance, phrases)
        else:
            content, messages = '{"labels": {}}', None
        labels = parse_labels(content, phrases)
        if content is None:
            print(f"❌ 实例 {instance['instance_id']} 调用失败，属性记为 unknown")
    except Exception as error:
        print(f"❌ 实例 {instance['instance_id']} 判断失败：{error}")
        labels = {"yes": [], "no": [], "unknown": list(phrases)}
        messages = None
    return {
        **instance,
        **labels,
        "input_tokens": messages.get("input_tokens") if messages else None,
        "output_tokens": messages.get("output_tokens") if messages else None,
    }


async def run(args):
    instances = load_json(args.instances)
    candidates = load_json(args.candidates)
    if any(not isinstance(captions, dict) for captions in candidates.values()):
        raise ValueError("候选文件不是图片 -> caption -> 属性列表格式，请重新运行 propose_predicates.py")
    if args.output.exists():
        results = load_json(args.output)
        if any("yes" not in row or "no" not in row or "unknown" not in row for row in results):
            raise ValueError("已有判断文件为旧 labels 格式，请使用新输出文件重新判断")
        completed = {row["instance_id"] for row in results}
        print(f"发现已有结果：跳过 {len(completed)} 个已完成实例")
    else:
        results = []
        completed = set()
    if args.output.resolve() in {args.candidates.resolve(), args.instances.resolve()}:
        raise ValueError("判断结果不能覆盖候选或实例文件")

    # 摊平成一个列表后整体分批，不再按图片逐个处理。
    work = build_work_list(instances, candidates, completed, args.max_images)
    if not work:
        print("没有需要判断的实例")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"待判断实例 {len(work)} 条，批大小 {args.batch_size}")

    # Cropper 的密度索引使用完整实例表构建，与开放式提取流程一致。
    cropper = AdaptiveCropper.from_instances(instances, CropConfig(**CONFIG["crop"]))
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置 DASHSCOPE_API_KEY")
    judge = PredicateJudge("predicate_judge", api_key, args.base_url,
                           args.model, args.image_dir, cropper)

    # 与 main.py 相同：划分批次，批内并发，单条失败不影响其他实例。
    for start in range(0, len(work), args.batch_size):
        batch = work[start:start + args.batch_size]
        tasks = [judge_one(judge, instance, phrases) for instance, phrases in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        # 每批存盘一次，中断后重新运行可只处理剩余实例。
        save_json(args.output, results)
        print(f"✅ 已完成 {len(results)}/{len(work)}")

    counts = {label: sum(len(row[label]) for row in results)
              for label in ("yes", "no", "unknown")}
    print(f"三值判断完成：yes={counts['yes']} no={counts['no']} unknown={counts['unknown']}，"
          f"结果保存到 {args.output}")


def parse_args():
    parser = argparse.ArgumentParser(description="对每个实例进行闭集属性三值判断")
    parser.add_argument("--instances", type=Path, default=DEFAULT_INSTANCES)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image-dir", default=CONFIG["image_dir"])
    parser.add_argument("--model", default=CONFIG["model_name"])
    parser.add_argument("--base-url", default=DEFAULT_API_URL)
    parser.add_argument("--batch-size", type=int, default=CONFIG["process_batch_size"])
    parser.add_argument("--max-images", type=int, default=10, help="0表示全部候选图片")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
