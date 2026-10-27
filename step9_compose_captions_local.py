"""step9 本地入口：与在线共用两段纯文本 prompt 和全部输出/检查逻辑。

    python step9_compose_captions_local.py --limit 10 --model-mode full
    accelerate launch step9_compose_captions_local.py --batch-size 8

复用 LocalAttributesExtractor 的模型加载、生成和置信度统计；每卡完整副本，
只由主进程按批写入，不构造 cropper，不读取图片。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from recsub.caption import (
    caption_record, completed_captions, compose_prompt, load_caption_work,
    generated_caption, parse_attribute_phrase, parse_language_review, review_prompt, validate_paths,
)
from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.extract import file_digest, run_fingerprint
from recsub.local_runtime import create_accelerator, ordered_records, validate_runtime
from recsub.prompts import CAPTION_LANGUAGE_REVIEW_PROMPT, COMPOSE_CAPTION_PROMPT
from step3_extract_attributes_local import append_batch, get_process_batch
from step5_judge_predicates_local import _usage


def caption_fingerprint(args: argparse.Namespace, world_size: int = 1) -> str:
    return run_fingerprint({
        "backend": "local", "subsets_sha256": file_digest(args.subsets),
        "instances_sha256": file_digest(args.instances),
        "model_path": str(Path(args.model_path).resolve()),
        "base_model_path": str(Path(args.base_model_path or args.model_path).resolve()),
        "adapter_path": str(Path(args.adapter_path).resolve()) if args.adapter_path else None,
        "mode": args.model_mode, "dtype": args.dtype,
        **{key: getattr(args, key) for key in (
            "enable_thinking", "max_new_tokens", "do_sample", "temperature", "top_p", "top_k",
            "min_p", "presence_penalty", "repetition_penalty", "record_token_confidences",
        )},
        "world_size": world_size, "batch_size_per_rank": args.batch_size,
        "compose_prompt": COMPOSE_CAPTION_PROMPT,
        "review_prompt": CAPTION_LANGUAGE_REVIEW_PROMPT,
    })


def infer_text_batch(items: list[dict], prompts: list[str], extractor) -> list[dict]:
    """沿用 step3/5 的批失败逐条重试，不将模型失败当成语言 no。"""
    if not items:
        return []
    try:
        generated = extractor.infer_batch(items, prompts=prompts, text_only=True)
    except Exception:
        generated = []
    responses = {row["instance_id"]: row for row in generated
                 if isinstance(row, Mapping) and "instance_id" in row}
    result = []
    for item, prompt in zip(items, prompts):
        response = responses.get(item["instance_id"])
        if response is None:
            try:
                single = extractor.infer_batch([item], prompts=[prompt], text_only=True)
                response = single[0] if single else None
            except Exception as error:
                response = {"generation_failed": True, "reason": f"本地生成失败: {error}"}
        if not isinstance(response, Mapping):
            response = {"generation_failed": True, "reason": "未返回推理结果"}
        result.append(dict(response))
    return result


def _content(response: Mapping) -> str | None:
    content = response.get("content")
    if response.get("generation_failed") or not isinstance(content, str) or not content.strip():
        return None
    return content


def compose_local_batch(items: list[dict], extractor) -> list[dict]:
    if not items:
        return []
    responses = infer_text_batch(items, [compose_prompt(item) for item in items], extractor)
    records: dict[str, dict] = {}
    pending: list[dict] = []
    for item, response in zip(items, responses):
        content = _content(response)
        phrase = parse_attribute_phrase(content) if content else None
        generated = generated_caption(item["category"], phrase) if phrase is not None else None
        reason = None
        if content is None:
            reason = response.get("reason") or "属性合成未生成有效文本"
        elif generated is None:
            reason = "属性短语输出解析失败"
        # 在第二次推理前记录 in 检查结果，包括 no。
        records[item["instance_id"]] = caption_record(
            item, generated=generated, reason=reason, generation_usage=_usage(response),
        )
        if generated is not None:
            pending.append(item)
    prompts = [review_prompt(item, records[item["instance_id"]]) for item in pending]
    reviews = infer_text_batch(pending, prompts, extractor)
    for item, response in zip(pending, reviews):
        content = _content(response)
        check = parse_language_review(content) if content else None
        reason = None
        if content is None:
            reason = response.get("reason") or "语言审核未生成有效文本"
        elif check is None:
            reason = "语言审核输出不是 yes/no"
        previous = records[item["instance_id"]]
        records[item["instance_id"]] = caption_record(
            item, generated={key: previous[key] for key in ("new_attributes", "new_caption")},
            language_check=check, reason=reason,
            generation_usage=previous.get("generation_usage"), review_usage=_usage(response),
        )
    return [records[item["instance_id"]] for item in items]


def run(args: argparse.Namespace, *, accelerator=None, extractor_factory=None) -> int:
    if accelerator is None:
        accelerator = create_accelerator()
    validate_runtime(accelerator, args.batch_size)
    if not args.check_fingerprint:
        accelerator.print("警告：已关闭运行指纹校验，续跑结果不会校验配置一致性。")
    completed = completed_captions(
        args.output, caption_fingerprint(args, accelerator.num_processes),
        claim=accelerator.is_main_process, check_fingerprint=args.check_fingerprint,
    )
    image_names = ({name.strip() for name in args.image_names.split(",") if name.strip()}
                   if args.image_names else None)
    items = load_caption_work(args.subsets, args.instances, completed=completed,
                              image_names=image_names, limit=args.limit)
    accelerator.wait_for_everyone()
    if not items:
        accelerator.print("没有需要合成的子集")
        return 0
    if extractor_factory is None:
        from recsub.llm_local import LocalAttributesExtractor
        extractor_factory = LocalAttributesExtractor
    extractor = extractor_factory(
        model_path=args.model_path, base_model_path=args.base_model_path,
        adapter_path=args.adapter_path, model_mode=args.model_mode, dtype=args.dtype,
        image_dir="", cropper=None, accelerator=accelerator,
        **{key: getattr(args, key) for key in (
            "enable_thinking", "max_new_tokens", "do_sample", "temperature", "top_p", "top_k",
            "min_p", "presence_penalty", "repetition_penalty", "record_token_confidences",
        )},
    )
    from accelerate.utils import gather_object
    size = args.batch_size * accelerator.num_processes
    counters = Counter()
    for start in range(0, len(items), size):
        batch = items[start:start + size]
        local = get_process_batch(batch, args.batch_size, accelerator.process_index)
        records = compose_local_batch(local, extractor)
        # 仅传输阶段临时带 instance_id，复用 rank 排序工具，写盘前移除。
        gathered = gather_object([{**record, "instance_id": item["instance_id"]}
                                  for item, record in zip(local, records)])
        if accelerator.is_main_process:
            ordered = ordered_records(batch, gathered,
                                      lambda item, reason: caption_record(item, reason=reason))
            for record in ordered:
                record.pop("instance_id", None)
                counters["accepted"] += bool(record["accepted"])
                counters["failed"] += bool(record.get("failed"))
                counters["language_no"] += record.get("language_check") == "no"
                counters["string_no"] += record.get("string_check") == "no"
            append_batch(args.output, ordered)
            accelerator.print(f"  进度 {start + len(batch)}/{len(items)}  "
                              f"通过 {counters['accepted']} 失败 {counters['failed']}")
        accelerator.wait_for_everyone()
    accelerator.print(f"✅ 审核通过 {counters['accepted']}，审核 no {counters['language_no']}，"
                      f"字符串 no {counters['string_no']}，失败 {counters['failed']}；结果 → {args.output}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    # 本地模型参数直接复用 step3 的 argparse 定义，只保留 step9 使用的参数。
    from step3_extract_attributes_local import parse_args as extraction_args
    defaults = extraction_args([])
    parser = argparse.ArgumentParser(description="子集属性短语与 caption 合成及语言审核（本地 Qwen）")
    parser.add_argument("--subsets", type=Path, default=path("caption_subsets"))
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--output", type=Path, default=path("captions_local"))
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--limit", type=int, default=CONFIG["caption"]["limit"])
    parser.add_argument("--batch-size", type=int, default=defaults.batch_size,
                        help="每张 GPU 的子集数")
    for name in ("model_path", "base_model_path", "adapter_path"):
        parser.add_argument("--" + name.replace("_", "-"), default=getattr(defaults, name))
    parser.add_argument("--model-mode", choices=("base", "nf4", "full", "lora", "qlora"),
                        default=defaults.model_mode)
    parser.add_argument("--dtype", choices=("bfloat16", "float16", "float32"), default=defaults.dtype)
    for name in ("enable_thinking", "do_sample", "record_token_confidences", "check_fingerprint"):
        parser.add_argument("--" + name.replace("_", "-"), action=argparse.BooleanOptionalAction,
                            default=getattr(defaults, name))
    for name, kind in (("max_new_tokens", int), ("top_k", int), ("temperature", float),
                       ("top_p", float), ("min_p", float), ("repetition_penalty", float)):
        parser.add_argument("--" + name.replace("_", "-"), type=kind, default=getattr(defaults, name))
    parser.add_argument("--presence-penalty", type=float,
                        default=CONFIG["caption"]["presence_penalty_local"])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        validate_paths(args)
        if args.max_new_tokens < 1:
            raise ValueError("max-new-tokens 必须 ≥1")
        return run(args)
    except KeyboardInterrupt:
        print("已中断；结果已按批写入，重跑会自动续接", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 本地属性合成失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
