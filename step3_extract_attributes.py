"""step3：引导式开放提取（原 step2）。

与原版的关键差别：

1. **提示词里带一段来自标注词表的参考词表**做风格引导，但仍是开放提取——
   强制闭集会让模型放弃词表里没有、却真有区分力的特征。该参考词表按实例
   类别挑选，并**剔除该实例父 caption 自己的槽位及别名**，否则模型会把父
   属性再写一遍（原数据里 253 个「caption－候选词」组合有 55 个是这种）。
2. **输出改 JSONL 追加写**。旧实现每写一条都要读回整个文件再整体写回，在
   28.6 万条上是 O(n²) 的 IO；结果文件已经长到 251MB。
3. **失败可重试**。调用失败的实例写 `failed: true` 记录但不计入已完成集合，
   下次运行自动重试。
4. **可以不存思维链**（`--keep-reasoning`，默认关）。思维链在旧结果里占了
   绝大部分体积，而它只在调试时才需要。

    # 先用一小批验证提示词与 few-shot 是否正确
    python step3_extract_attributes.py --image-names 0000-fsc147-1106.jpg,0001-fsc147-1306.jpg
    # 分层抽样试跑
    python step3_extract_attributes.py --sample-per-category 200 --seed 13
    # 全量
    python step3_extract_attributes.py
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.io import (
    close_all,
    get_writer,
    iter_jsonl,
    write_json_atomic,
)
from recsub.llm import OnlineModel, build_cropper, load_three_views
from recsub.prompts import build_extract_attributes_prompt, build_instance_messages
from recsub.utils import match_json
from recsub.vocabulary import Vocabulary

ALLOWED_FIELDS = ("appearance", "action", "location", "relation")


def parse_attributes(content: str) -> dict[str, list[str]] | None:
    """校验模型输出：只允许四个字段，每个值都是字符串列表。"""
    parsed = match_json(content)
    if not isinstance(parsed, dict):
        return None
    if not set(parsed) <= set(ALLOWED_FIELDS):
        return None
    if any(not isinstance(value, list) or any(not isinstance(item, str) for item in value)
           for value in parsed.values()):
        return None
    return {field: [item.strip() for item in parsed.get(field, []) if item.strip()]
            for field in ALLOWED_FIELDS if parsed.get(field)}


def select_work(
        instance_path: Path,
        *,
        image_names: set[str] | None,
        sample_per_category: int,
        seed: int,
        completed: set[int],
        sample_output: Path | None,
) -> tuple[set[int], dict[str, int]]:
    """决定本次要处理哪些 instance_id。

    抽样按类别分层，并把选中的 id 写盘，保证同一 seed 的重复运行选取同一批数据。
    """
    by_category: dict[str, list[int]] = defaultdict(list)
    for instance in iter_jsonl(instance_path):
        instance_id = instance["instance_id"]
        if instance_id in completed:
            continue
        if image_names is not None and instance["image_name"] not in image_names:
            continue
        by_category[instance["category"]].append(instance_id)

    if sample_per_category <= 0:
        selected = {instance_id for ids in by_category.values() for instance_id in ids}
        stats = {"categories": len(by_category), "candidates": len(selected), "sampled": 0}
    else:
        rng = random.Random(seed)
        selected = set()
        for category in sorted(by_category):
            ids = by_category[category]
            selected.update(ids if len(ids) <= sample_per_category
                            else rng.sample(ids, sample_per_category))
        stats = {"categories": len(by_category), "candidates": sum(map(len, by_category.values())),
                 "sampled": len(selected)}

    if sample_output is not None and sample_per_category > 0:
        write_json_atomic(sample_output, {
            "seed": seed, "sample_per_category": sample_per_category,
            "image_names": sorted(image_names) if image_names else None,
            "instance_ids": sorted(selected),
        })
    return selected, stats


def iter_selected(instance_path: Path, selected: set[int]) -> Iterator[dict]:
    for instance in iter_jsonl(instance_path):
        if instance["instance_id"] in selected:
            yield instance


async def extract_one(online: OnlineModel, cropper, instance: Mapping[str, Any],
                      text_prompt: str, image_dir: str) -> dict:
    """处理一个实例，返回待写盘的记录（失败时带 ``failed`` 标记）。"""
    base = {
        "instance_id": instance["instance_id"],
        "image_name": instance["image_name"],
        "category": instance["category"],
        "caption": instance["caption"],
    }
    try:
        global_image, context_image, fine_image, _ = await asyncio.to_thread(
            load_three_views, instance, image_dir, cropper
        )
    except Exception as error:
        return {**base, "failed": True, "reason": f"裁剪失败: {error}"}

    completion = await online.complete(build_instance_messages(
        global_image, context_image, fine_image, text_prompt
    ))
    if completion is None:
        return {**base, "failed": True, "reason": "在线调用失败"}

    attributes = parse_attributes(completion.content)
    if attributes is None:
        return {**base, "failed": True, "reason": "输出解析失败",
                **completion.usage(), "attempts": completion.attempts}

    record = {**base, "attributes": attributes, **completion.usage(),
              "attempts": completion.attempts}
    return record


async def run(args: argparse.Namespace) -> int:
    vocabulary = Vocabulary.load(args.vocabulary)
    print(f"✅ 词表 {len(vocabulary)} 条，类别 {len(vocabulary.categories())} 个")

    image_names = None
    if args.image_names:
        image_names = {name.strip() for name in args.image_names.split(",") if name.strip()}

    completed: set[int] = set()
    legacy_present = False
    if args.output.is_file():
        for record in iter_jsonl(args.output, strict=False):
            is_new = (
                    isinstance(record.get("instance_id"), int)
                    and all(field in record for field in ("image_name", "caption", "category"))
                    and isinstance(record.get("attributes"), Mapping)
            )
            if is_new and not record.get("failed"):
                completed.add(int(record["instance_id"]))
            elif not is_new:
                legacy_present = True
    if legacy_present:
        raise RuntimeError(
            f"{args.output} 含旧版提取记录（attributes 是原始字符串、结果在 more_attributes），"
            "不能与 step3 新格式混写；请用 --output anno/extract_result_v2.jsonl "
            "重新生成，旧记录可直接交给 step4 兼容读取"
        )
    if completed:
        print(f"发现已有新格式结果：跳过 {len(completed)} 个已完成实例")

    # 密度索引必须用完整实例表构建，抽样只决定训练哪些实例。
    cropper = build_cropper(iter_jsonl(args.instances))
    print(f"✅ Crop 索引：{cropper.group_count} 组，{cropper.unique_point_count} 个唯一点")

    selected, stats = select_work(
        args.instances,
        image_names=image_names,
        sample_per_category=args.sample_per_category,
        seed=args.seed,
        completed=completed,
        sample_output=path("extract_sample") if args.sample_per_category > 0 else None,
    )
    if not selected:
        print("没有需要处理的实例")
        return 0
    if args.sample_per_category > 0:
        print(f"分层抽样：{stats['categories']} 个类别 / {stats['candidates']} 个候选 → "
              f"选中 {stats['sampled']} 个（seed={args.seed}）")
    print(f"⏳ 待处理 {len(selected)} 个实例，批大小 {args.batch_size}")

    online = OnlineModel.create(
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        enable_thinking=args.enable_thinking,
    )
    # 提示词按 (类别, 父槽位) 缓存：few-shot 必须剔除父槽位，因此不能只按类别缓存。
    prompt_cache: dict[tuple[str, str], str] = {}
    preview: dict[str, Any] = {}
    writer = get_writer(args.output)

    processed = succeeded = failed = 0
    input_tokens = output_tokens = 0
    writer_used = False

    async def handle(instance: Mapping[str, Any]) -> None:
        nonlocal succeeded, failed, input_tokens, output_tokens, writer_used
        category = instance["category"]
        parent = vocabulary.parent_slot(category, instance.get("type"), instance["attributes"])
        cache_key = (category, parent.predicate_id)
        text_prompt = prompt_cache.get(cache_key)
        if text_prompt is None:
            examples = vocabulary.fewshot(
                category, exclude_slots=[parent],
                per_dimension=args.fewshot_per_dimension,
                total=args.fewshot_total,
            )
            text_prompt = build_extract_attributes_prompt(
                category, instance["attributes"],
                vocabulary_examples=examples, max_per_field=args.max_phrases_per_field,
            )
            prompt_cache[cache_key] = text_prompt
            # 只保存有限份预览；全量 person 的父槽位很多，若把每个完整提示词都存下来，
            # 预览文件会重新膨胀到几十 MB，失去调试用途。
            if len(preview) < args.max_preview:
                preview.setdefault(cache_key[1], {
                    "category": category, "parent_slot": parent.predicate_id,
                    "excluded_slot": parent.display,
                    "vocabulary_examples": examples, "prompt": text_prompt,
                })

        record = await extract_one(online, cropper, instance, text_prompt, args.image_dir)
        await writer.append(record)
        writer_used = True
        if record.get("failed"):
            failed += 1
        else:
            succeeded += 1
            input_tokens += record.get("input_tokens") or 0
            output_tokens += record.get("output_tokens") or 0

    batch_size = max(1, args.batch_size)
    pending: list[Mapping[str, Any]] = []
    for instance in iter_selected(args.instances, selected):
        pending.append(instance)
        if len(pending) < batch_size:
            continue
        await asyncio.gather(*(handle(item) for item in pending))
        processed += len(pending)
        pending = []
        print(f"  进度 {processed}/{len(selected)}  成功 {succeeded} 失败 {failed}")
    if pending:
        await asyncio.gather(*(handle(item) for item in pending))
        processed += len(pending)

    if writer_used:
        close_all()

    if preview:
        write_json_atomic(args.prompt_preview, preview)
        print(f"✅ 提示词预览（每类别一份，含 few-shot）→ {args.prompt_preview}")

    print(f"✅ 处理 {processed} 个实例：成功 {succeeded}，失败 {failed}")
    if failed:
        print(f"⚠️ {failed} 个失败实例写了 failed 标记且未计入已完成，重跑会自动重试")
    if succeeded:
        print(f"   token：input {input_tokens}，output {output_tokens}，"
              f"平均 output {output_tokens / succeeded:.0f}")
    print(f"   结果 → {args.output}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="引导式开放属性提取")
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="实例表（step1 产出）")
    parser.add_argument("--vocabulary", type=Path, default=path("vocabulary"),
                        help="谓词词表（step2 产出）")
    parser.add_argument("--image-dir", default=CONFIG["image_dir"],
                        help="原图目录")
    parser.add_argument("--output", type=Path, default=path("extract_result"),
                        help="提取结果 JSONL（追加写，可中断续跑）")
    parser.add_argument("--prompt-preview", type=Path, default=Path("anno/extract_prompt_preview.json"),
                        help="每个 (类别, 父槽位) 存一份提示词，便于核对 few-shot")
    parser.add_argument("--image-names", default=None,
                        help="只处理这些图片（逗号分隔），用于小样本验证")
    parser.add_argument("--sample-per-category", type=int, default=0,
                        help="每类别分层抽样 N 个实例（0 表示全部）")
    parser.add_argument("--seed", type=int, default=13, help="分层抽样随机种子")
    parser.add_argument("--batch-size", type=int, default=CONFIG["llm"]["batch_size"])
    parser.add_argument("--concurrency", type=int, default=None, help="并发上限，默认取配置")
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--fewshot-per-dimension", type=int,
                        default=CONFIG["vocabulary"]["max_fewshot_per_dimension"])
    parser.add_argument("--fewshot-total", type=int,
                        default=CONFIG["vocabulary"]["max_fewshot_total"])
    parser.add_argument("--max-phrases-per-field", type=int, default=2,
                        help="提示词里要求每个字段最多给出几条")
    parser.add_argument("--max-preview", type=int, default=100,
                        help="最多保存多少份完整提示词预览，避免全量运行时文件膨胀")
    parser.add_argument("--enable-thinking", dest="enable_thinking", action="store_true",
                        default=CONFIG["llm"]["enable_thinking"])
    parser.add_argument("--no-thinking", dest="enable_thinking", action="store_false",
                        help="关闭思维链（省 output token，需要实测质量）")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    for name in ("instances", "vocabulary"):
        if not getattr(args, name).is_file():
            print(f"❌ 找不到 {name}：{getattr(args, name)}", file=sys.stderr)
            return 1
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("已中断；结果已按条落盘，重跑会自动续接", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 提取失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
