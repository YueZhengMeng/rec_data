"""step3 在线入口：用无词表的开放式 prompt 提取目标实例的可见属性。

    python step3_extract_attributes_online.py --image-names sample.jpg --no-enable-thinking
    python step3_extract_attributes_online.py --sample-per-category 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, crop_config, online_sampling, path
from recsub.console import configure_console
from recsub.extract import (
    completed_ids, extraction_record, file_digest, iter_selected, parse_attributes,
    run_fingerprint, select_work,
)
from recsub.io import close_all, get_writer, iter_jsonl, write_json_atomic
from recsub.llm_api import OnlineModel, build_cropper, load_three_views
from recsub.prompts import (
    EXTRACT_ATTRIBUTES_PROMPT, build_extract_attributes_prompt, )
from recsub.prompts import build_instance_messages


def fingerprint_for(args: argparse.Namespace) -> str:
    return run_fingerprint({
        "backend": "online", "provider": CONFIG["provider"],
        "instances_sha256": file_digest(args.instances),
        "image_dir": str(Path(args.image_dir).resolve()),
        "model": CONFIG["model_name"], "base_url": CONFIG["base_url"],
        "enable_thinking": args.enable_thinking,
        "sampling": online_sampling(enable_thinking=args.enable_thinking),
        "crop": asdict(crop_config()), "prompt": EXTRACT_ATTRIBUTES_PROMPT,
    })


async def extract_one(online: OnlineModel, cropper, instance: Mapping[str, Any],
                      text_prompt: str, image_dir: str) -> dict:
    try:
        global_image, context_image, fine_image, _ = await asyncio.to_thread(
            load_three_views, instance, image_dir, cropper
        )
    except Exception as error:
        return extraction_record(instance, reason=f"裁剪失败: {error}")

    completion = await online.complete(build_instance_messages(
        global_image, context_image, fine_image, text_prompt
    ))
    if completion is None:
        return extraction_record(instance, reason="在线调用失败")

    attributes = parse_attributes(completion.content)
    return extraction_record(
        instance, attributes=attributes, reason="输出解析失败",
        usage={**completion.usage(), "attempts": completion.attempts},
    )


async def run(args: argparse.Namespace) -> int:
    if not args.check_fingerprint:
        print(
            "警告：已关闭运行指纹校验（--no-check-fingerprint），续跑结果不会校验配置一致性。"
        )
    image_names = ({name.strip() for name in args.image_names.split(",") if name.strip()}
                   if args.image_names else None)
    fingerprint = fingerprint_for(args)
    completed = completed_ids(args.output, fingerprint, check_fingerprint=args.check_fingerprint)
    if completed:
        print(f"跳过 {len(completed)} 个已完成实例")

    # 密度索引用完整实例表，抽样只决定实际发给模型的点。
    selected, stats = select_work(
        args.instances, image_names=image_names, sample_per_category=args.sample_per_category,
        seed=args.seed, completed=completed,
        sample_output=args.sample_output if args.sample_per_category > 0 else None,
    )
    if not selected:
        print("没有需要处理的实例")
        return 0
    cropper = build_cropper(iter_jsonl(args.instances))
    print(f"✅ Crop 索引：{cropper.group_count} 组，{cropper.unique_point_count} 个唯一点")
    if args.sample_per_category > 0:
        print(f"分层抽样：{stats['categories']} 个类别 / {stats['candidates']} 个候选 → "
              f"选中 {stats['sampled']} 个（seed={args.seed}）")
    online = OnlineModel.create(concurrency=args.concurrency, max_retries=args.max_retries,
                                enable_thinking=args.enable_thinking)
    writer = get_writer(args.output)
    prompt_cache: dict[tuple[str, str], str] = {}
    preview: dict[str, Any] = {}
    succeeded = failed = input_tokens = output_tokens = 0
    processed = 0

    async def handle(instance: Mapping[str, Any]) -> None:
        nonlocal succeeded, failed, input_tokens, output_tokens
        cache_key = (str(instance["category"]), str(instance["attributes"]))
        text_prompt = prompt_cache.get(cache_key)
        if text_prompt is None:
            text_prompt = build_extract_attributes_prompt(*cache_key)
            prompt_cache[cache_key] = text_prompt
            if len(preview) < args.max_preview:
                preview[json.dumps(cache_key, ensure_ascii=False)] = {
                    "category": cache_key[0], "known_attributes": cache_key[1],
                    "prompt": text_prompt,
                }
        record = await extract_one(online, cropper, instance, text_prompt, args.image_dir)
        await writer.append(record)
        if record.get("failed"):
            failed += 1
        else:
            succeeded += 1
            input_tokens += record.get("input_tokens") or 0
            output_tokens += record.get("output_tokens") or 0

    try:
        pending: list[dict] = []
        for instance in iter_selected(args.instances, selected):
            pending.append(instance)
            if len(pending) < args.batch_size:
                continue
            await asyncio.gather(*(handle(item) for item in pending))
            processed += len(pending)
            pending = []
            print(f"  进度 {processed}/{len(selected)}  成功 {succeeded} 失败 {failed}")
        if pending:
            await asyncio.gather(*(handle(item) for item in pending))
            processed += len(pending)
    finally:
        close_all()

    if preview:
        write_json_atomic(args.prompt_preview, preview)
    print(f"✅ 处理 {processed} 个实例：成功 {succeeded}，失败 {failed}")
    if succeeded:
        print(f"   token：input {input_tokens}，output {output_tokens}")
    print(f"   结果 → {args.output}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在线开放属性提取")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--image-dir", default=CONFIG["image_dir"])
    parser.add_argument("--output", type=Path, default=path("extract_result_online"))
    parser.add_argument("--check-fingerprint", action=argparse.BooleanOptionalAction,
                        default=CONFIG["resume"]["check_fingerprint"],
                        help="校验输出旁挂的运行指纹；关闭后不会读取既有标记")
    parser.add_argument("--sample-output", type=Path, default=path("extract_sample_online"))
    parser.add_argument("--prompt-preview", type=Path, default=path("extract_preview_online"))
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--sample-per-category", type=int,
                        default=CONFIG["extract"]["sample_per_category_online"],
                        help="每类抽样个数；0 表示全量")
    parser.add_argument("--seed", type=int, default=CONFIG["extract"]["seed_online"])
    parser.add_argument("--batch-size", type=int, default=CONFIG["online"]["batch_size"])
    parser.add_argument("--concurrency", type=int, default=None,
                        help="并发请求数；留空用 config 的 online.concurrency")
    parser.add_argument("--max-retries", type=int, default=None,
                        help="单次调用重试次数；留空用 config 的 online.max_retries")
    parser.add_argument("--max-preview", type=int, default=CONFIG["extract"]["max_preview"])
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction,
                        default=CONFIG["online"]["enable_thinking"],
                        help="是否启用思维链；--no-enable-thinking 关闭")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if not args.instances.is_file():
        print(f"❌ 找不到实例表：{args.instances}", file=sys.stderr)
        return 1
    if args.batch_size < 1 or args.sample_per_category < 0 or args.max_preview < 0:
        print("❌ batch-size 必须 ≥1；抽样数和预览数必须 ≥0", file=sys.stderr)
        return 1
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("已中断；结果已逐条写入", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 提取失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
