"""step9 在线入口：LLM 生成属性短语 → 代码拼 caption 并检查 in → LLM yes/no 审核。

    python step9_compose_captions_online.py --limit 10 --check-fingerprint
    python step9_compose_captions_online.py --subsets anno/final_subsets.jsonl

两次调用均为纯文本，不读取图片，不重新判断子集成员资格。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from recsub.caption import (
    caption_record, completed_captions, compose_prompt, load_caption_work,
    generated_caption, parse_attribute_phrase, parse_language_review, review_prompt, validate_paths,
)
from recsub.config import CONFIG, online_sampling, path
from recsub.console import configure_console
from recsub.extract import file_digest, run_fingerprint
from recsub.io import close_all, get_writer
from recsub.llm_api import OnlineModel
from recsub.online_batch import run_online_batch
from recsub.prompts import CAPTION_LANGUAGE_REVIEW_PROMPT, COMPOSE_CAPTION_PROMPT

# 与 step5 一样允许属性词重复；DeepSeek 不发送该参数。
PRESENCE_PENALTY = 0.0


def caption_fingerprint(args: argparse.Namespace) -> str:
    return run_fingerprint({
        "backend": "online", "provider": CONFIG["provider"],
        "subsets_sha256": file_digest(args.subsets),
        "instances_sha256": file_digest(args.instances),
        "model": CONFIG["model_name"], "base_url": CONFIG["base_url"],
        "enable_thinking": args.enable_thinking,
        "sampling": online_sampling(enable_thinking=args.enable_thinking,
                                    presence_penalty=PRESENCE_PENALTY),
        "compose_prompt": COMPOSE_CAPTION_PROMPT,
        "review_prompt": CAPTION_LANGUAGE_REVIEW_PROMPT,
    })


async def compose_one(online: OnlineModel, item: Mapping) -> dict:
    completion = await online.complete([{"role": "user", "content": compose_prompt(item)}])
    if completion is None:
        return caption_record(item, reason="属性合成在线调用失败")
    usage = {**completion.usage(), "attempts": completion.attempts}
    phrase = parse_attribute_phrase(completion.content)
    if phrase is None:
        return caption_record(item, reason="属性短语输出解析失败",
                              generation_usage=usage)
    generated = generated_caption(item["category"], phrase)
    # 先完成代码检查，no 也进入语言审核；不把 literal in 当成语义保留的充分条件。
    draft = caption_record(item, generated=generated, generation_usage=usage)
    review = await online.complete([{"role": "user", "content": review_prompt(item, draft)}])
    if review is None:
        return caption_record(item, generated=generated, reason="语言审核在线调用失败",
                              generation_usage=usage)
    language_check = parse_language_review(review.content)
    return caption_record(
        item, generated=generated, language_check=language_check,
        reason="语言审核输出不是 yes/no" if language_check is None else None,
        generation_usage=usage, review_usage={**review.usage(), "attempts": review.attempts},
    )


async def run(args: argparse.Namespace) -> int:
    if not args.check_fingerprint:
        print("警告：已关闭运行指纹校验，续跑结果不会校验配置一致性。")
    completed = completed_captions(args.output, caption_fingerprint(args),
                                   check_fingerprint=args.check_fingerprint)
    image_names = ({name.strip() for name in args.image_names.split(",") if name.strip()}
                   if args.image_names else None)
    items = load_caption_work(args.subsets, args.instances, completed=completed,
                              image_names=image_names, limit=args.limit)
    if not items:
        print("没有需要合成的子集")
        return 0
    print(f"✅ 已完成 {len(completed)} 个子集；待合成 {len(items)} 个（每个子集两次纯文本调用）")
    online = OnlineModel.create(concurrency=args.concurrency, max_retries=args.max_retries,
                                enable_thinking=args.enable_thinking,
                                presence_penalty=PRESENCE_PENALTY)
    writer = get_writer(args.output)
    counters = Counter()

    async def handle(item: Mapping) -> None:
        record = await compose_one(online, item)
        await writer.append(record)
        counters["written"] += 1
        counters["failed"] += bool(record.get("failed"))
        counters["accepted"] += bool(record["accepted"])
        counters["string_no"] += record.get("string_check") == "no"
        counters["language_no"] += record.get("language_check") == "no"
        for token in ("input_tokens", "output_tokens"):
            counters[token] += record.get(token) or 0

    total_batches = (len(items) + args.batch_size - 1) // args.batch_size
    try:
        for number, start in enumerate(range(0, len(items), args.batch_size), 1):
            await run_online_batch(items[start:start + args.batch_size], handle,
                                   desc=f"step9 batch {number}/{total_batches}")
    finally:
        close_all()
    print(f"✅ 合成 {counters['written']}：审核通过 {counters['accepted']}，"
          f"审核 no {counters['language_no']}，调用/解析失败 {counters['failed']}")
    print(f"   字符串检查 no {counters['string_no']}（非硬门禁）；"
          f"token：input {counters['input_tokens']}，output {counters['output_tokens']}")
    print(f"   结果 → {args.output}；失败/审核 no 可续跑，也可用 step9_review_captions.py 人工确认")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="子集属性短语与 caption 合成及语言审核（在线）")
    parser.add_argument("--subsets", type=Path, default=path("caption_subsets"),
                        help="已完成划分/审核的子集 JSONL；需含 predicate.display")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--output", type=Path, default=path("captions_online"))
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--limit", type=int, default=CONFIG["caption"]["limit"],
                        help="只处理输入前 N 个子集；0 表示全量，先截取再排除已完成项")
    parser.add_argument("--batch-size", type=int, default=CONFIG["online"]["batch_size"])
    parser.add_argument("--concurrency", type=int, default=CONFIG["online"]["concurrency"])
    parser.add_argument("--max-retries", type=int, default=CONFIG["online"]["max_retries"])
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction,
                        default=CONFIG["online"]["enable_thinking"])
    parser.add_argument("--check-fingerprint", action=argparse.BooleanOptionalAction,
                        default=CONFIG["resume"]["check_fingerprint"])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        validate_paths(args)
        if args.concurrency < 1 or args.max_retries < 1:
            raise ValueError("concurrency/max-retries 必须 ≥1")
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("已中断；结果已逐条写入，重跑会自动续接", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 属性合成失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
