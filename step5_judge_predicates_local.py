"""step5：使用本地 Qwen 对候选谓词做逐实例三值判断。

判断 prompt、候选处理、探针剪枝和输出记录与
:mod:`step5_judge_predicates_online` 完全一致；只有图像输入和推理后端不同。

    python step5_judge_predicates_local.py --model-path F:/model/Qwen3.5-0.8B
    accelerate launch step5_judge_predicates_local.py --model-path F:/model/Qwen3.5-0.8B
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, crop_config, path
from recsub.console import configure_console
from recsub.crop import AdaptiveCropper
from recsub.extract import file_digest, run_fingerprint
from recsub.io import ensure_parent, iter_jsonl, write_json_atomic
from recsub.judgment import (
    build_work, completed_judgments, expand_labels, load_caption_predicates,
    parse_labels, ratio_prune, restrict_labels,
)
from recsub.local_runtime import (
    broadcast_object, create_accelerator, ordered_records, validate_runtime,
)
from recsub.prompts import PREDICATE_JUDGMENT_PROMPT, build_judgment_prompt


def judgment_fingerprint(args: argparse.Namespace, world_size: int = 1) -> str:
    """绑定输入、local 模型、生成参数、prompt 和判断参数。"""
    return run_fingerprint({
        "backend": "local",
        "candidates_sha256": file_digest(args.candidates),
        "instances_sha256": file_digest(args.instances),
        "image_dir": str(Path(args.image_dir).resolve()),
        "model_path": str(Path(args.model_path).resolve()),
        "base_model_path": str(Path(args.base_model_path or args.model_path).resolve()),
        "adapter_path": str(Path(args.adapter_path).resolve()) if args.adapter_path else None,
        "mode": args.model_mode, "dtype": args.dtype,
        "enable_thinking": args.enable_thinking,
        "max_new_tokens": args.max_new_tokens,
        "do_sample": args.do_sample,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "min_p": args.min_p,
        "presence_penalty": args.presence_penalty,
        "repetition_penalty": args.repetition_penalty,
        "probe_per_caption": args.probe_per_caption,
        "probe_min_instances": args.probe_min_instances,
        "constant_yes_ratio": args.constant_yes_ratio,
        "constant_no_ratio": args.constant_no_ratio,
        "world_size": world_size,
        "batch_size_per_rank": args.batch_size,
        "crop": asdict(crop_config()),
        "prompt": PREDICATE_JUDGMENT_PROMPT,
    })


def _usage(response: Mapping[str, Any]) -> dict[str, Any]:
    return {key: response[key] for key in (
        "input_tokens", "output_tokens", "average_confidence", "ppl",
        "output_token_ids", "token_confidences",
    ) if key in response and (
                    key not in ("output_token_ids", "token_confidences") or response[key]
            )}


def _record_from_response(item: Mapping[str, Any], response: Mapping[str, Any],
                          phrases: Sequence[str]) -> dict:
    base = {
        "instance_id": item["instance_id"],
        "image_name": item["image_name"],
        "caption": item["caption"],
        "category": item["category"],
    }
    usage = _usage(response)
    content = response.get("content")
    if response.get("generation_failed") or not isinstance(content, str) or not content.strip():
        return {**base, "judgment_failed": True, "reason": "模型未生成有效文本", **usage}
    labels = parse_labels(content, phrases)
    if labels is None:
        return {**base, "judgment_failed": True, "reason": "输出解析失败", **usage}
    return {**base, "labels": labels, **usage}


def judge_local_batch(items: Sequence[Mapping[str, Any]], phrases_by_root: Mapping,
                      extractor) -> list[dict]:
    """本地批量判断；批量失败时逐条重试，保证每条记录可续跑。"""
    if not items:
        return []
    pending = [item for item in items
               if phrases_by_root[(item["image_name"], item["caption"])]]
    if len(pending) != len(items):
        generated = judge_local_batch(pending, phrases_by_root, extractor)
        by_id = {record["instance_id"]: record for record in generated}
        return [by_id[item["instance_id"]] if item["instance_id"] in by_id else {
            **{key: item[key] for key in ("instance_id", "image_name", "caption", "category")},
            "labels": {}, "judgment_missing": True,
        } for item in items]
    prompts = [build_judgment_prompt(
        str(item.get("attributes") or ""), str(item["category"]),
        list(phrases_by_root[(item["image_name"], item["caption"])]),
    ) for item in items]
    try:
        generated = extractor.infer_batch(list(items), prompts=prompts)
    except Exception:
        generated = []

    responses = {
        item["instance_id"]: item for item in generated
        if isinstance(item, Mapping) and "instance_id" in item
    }
    records: list[dict] = []
    for index, item in enumerate(items):
        phrases = phrases_by_root[(item["image_name"], item["caption"])]
        response = responses.get(item["instance_id"])
        if response is None:
            try:
                single = extractor.infer_batch([dict(item)], prompts=[prompts[index]])
                response = single[0] if single else None
            except Exception as error:
                records.append({
                    "instance_id": item["instance_id"],
                    "image_name": item["image_name"],
                    "caption": item["caption"],
                    "category": item["category"],
                    "judgment_failed": True,
                    "reason": f"本地生成失败: {error}",
                })
                continue
        if not isinstance(response, Mapping):
            records.append({
                "instance_id": item["instance_id"],
                "image_name": item["image_name"],
                "caption": item["caption"],
                "category": item["category"],
                "judgment_failed": True,
                "reason": "未返回推理结果",
            })
            continue
        records.append(_record_from_response(item, response, phrases))

    return records


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="逐实例闭集三值判断（本地 Qwen）")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--candidates", type=Path, default=path("predicate_candidates_local"),
                        help="归一化候选（step4 产出的 JSONL）")
    parser.add_argument("--image-dir", default=CONFIG["image_dir"])
    parser.add_argument("--output", type=Path, default=path("predicate_labels_local"))
    parser.add_argument("--check-fingerprint", action=argparse.BooleanOptionalAction,
                        default=CONFIG["resume"]["check_fingerprint"],
                        help="校验输出旁挂的运行指纹；关闭后不会读取既有标记")
    parser.add_argument("--constant-output", type=Path,
                        default=path("predicate_constant_local"),
                        help="探针剪掉的「根×恒定条件」记录")
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--batch-size", type=int, default=CONFIG["local"]["batch_size"],
                        help="每张 GPU 的实例数")
    parser.add_argument("--model-path", default=CONFIG["local"]["model_path"])
    parser.add_argument("--base-model-path", default=CONFIG["local"]["base_model_path"])
    parser.add_argument("--adapter-path", default=CONFIG["local"]["adapter_path"])
    parser.add_argument("--model-mode", choices=("base", "nf4", "full", "lora", "qlora"),
                        default=CONFIG["local"]["model_mode"])
    parser.add_argument("--dtype", choices=("bfloat16", "float16", "float32"),
                        default=CONFIG["local"]["dtype"])
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction,
                        default=CONFIG["local"]["enable_thinking"])
    parser.add_argument("--max-new-tokens", type=int, default=CONFIG["local"]["max_new_tokens"])
    parser.add_argument("--do-sample", action=argparse.BooleanOptionalAction,
                        default=CONFIG["local"]["do_sample"])
    parser.add_argument("--temperature", type=float, default=CONFIG["local"]["temperature"])
    parser.add_argument("--top-p", type=float, default=CONFIG["local"]["top_p"])
    parser.add_argument("--top-k", type=int, default=CONFIG["local"]["top_k"])
    parser.add_argument("--min-p", type=float, default=CONFIG["local"]["min_p"])
    parser.add_argument("--presence-penalty", type=float,
                        default=CONFIG["local"]["presence_penalty"])
    parser.add_argument("--repetition-penalty", type=float,
                        default=CONFIG["local"]["repetition_penalty"])
    parser.add_argument("--record-token-confidences", action=argparse.BooleanOptionalAction,
                        default=CONFIG["local"]["record_token_confidences"])
    parser.add_argument("--probe-per-caption", type=int,
                        default=CONFIG["judge"]["probe_per_caption"],
                        help="探针轮每根先判这么多实例以剪掉恒定条件；0 表示全量判断")
    parser.add_argument("--probe-min-instances", type=int,
                        default=CONFIG["judge"]["probe_min_instances"],
                        help="探针至少要判这么多实例才敢剪枝，样本不足时宁可多花 token")
    parser.add_argument("--constant-yes-ratio", type=float,
                        default=CONFIG["judge"]["constant_yes_ratio"])
    parser.add_argument("--constant-no-ratio", type=float,
                        default=CONFIG["judge"]["constant_no_ratio"])
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, accelerator=None, extractor_factory=None) -> int:
    if accelerator is None:
        accelerator = create_accelerator()
    if not args.check_fingerprint:
        accelerator.print(
            "警告：已关闭运行指纹校验（--no-check-fingerprint），续跑结果不会校验配置一致性。"
        )
    validate_runtime(accelerator, args.batch_size)
    if extractor_factory is None:
        from recsub.llm_local import LocalAttributesExtractor
        extractor_factory = LocalAttributesExtractor

    by_root, phrases_by_root, ids_by_display = load_caption_predicates(args.candidates)
    fingerprint = judgment_fingerprint(args, accelerator.num_processes)
    completed, prior_rows = completed_judgments(
        args.output,
        fingerprint,
        claim=accelerator.is_main_process,
        check_fingerprint=args.check_fingerprint,
    )
    if prior_rows and args.probe_per_caption > 0:
        raise ValueError("探针模式在部分写入后不能安全重算剪枝；请指定新的 --output")

    image_names = ({name.strip() for name in args.image_names.split(",") if name.strip()}
                   if args.image_names else None)
    work = build_work(iter_jsonl(args.instances), phrases_by_root, completed, image_names)
    items = [item for group in work.values() for item in group]
    accelerator.print(f"✅ 候选根 {len(by_root)} 个；待判断 {len(items)} 个实例")
    if not items:
        accelerator.print("没有需要判断的实例")
        return 0

    cropper = AdaptiveCropper.from_instances(iter_jsonl(args.instances), crop_config())
    extractor = extractor_factory(
        model_path=args.model_path, base_model_path=args.base_model_path,
        adapter_path=args.adapter_path, model_mode=args.model_mode, dtype=args.dtype,
        image_dir=args.image_dir, cropper=cropper, accelerator=accelerator,
        enable_thinking=args.enable_thinking, max_new_tokens=args.max_new_tokens,
        do_sample=args.do_sample, temperature=args.temperature, top_p=args.top_p,
        top_k=args.top_k, min_p=args.min_p, presence_penalty=args.presence_penalty,
        repetition_penalty=args.repetition_penalty,
        record_token_confidences=args.record_token_confidences,
    )

    from accelerate.utils import gather_object
    process_batch_size = args.batch_size * accelerator.num_processes
    writer = args.output
    ensure_parent(writer)
    constant_pairs: dict[str, list[str]] = {}
    counters = Counter()

    def local_slice(batch: list[dict]) -> list[dict]:
        start = accelerator.process_index * args.batch_size
        return batch[start:start + args.batch_size]

    def dispatch(batch: list[dict], phrases: Mapping) -> list[dict]:
        local_records = judge_local_batch(local_slice(batch), phrases, extractor)
        return list(gather_object(local_records))

    def append_records(records: Sequence[dict], keep: Sequence[str] | None = None) -> None:
        if not accelerator.is_main_process or not records:
            return
        with writer.open("a", encoding="utf-8") as handle:
            for record in records:
                if keep is not None:
                    record = restrict_labels(record, keep)
                counters["written"] += 1
                if record.get("judgment_failed"):
                    counters["failed"] += 1
                else:
                    counters["judged"] += 1
                    counters.update((record.get("labels") or {}).values())
                key = (record["image_name"], record["caption"])
                record = expand_labels(record, ids_by_display.get(key, {}))
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def failure_record(item, reason):
        return {
            **{key: item[key] for key in ("instance_id", "image_name", "caption", "category")},
            "judgment_failed": True, "reason": reason,
        }

    def process_batches(batch_items: Sequence[dict], phrases: Mapping) -> None:
        for start in range(0, len(batch_items), process_batch_size):
            batch = list(batch_items[start:start + process_batch_size])
            gathered = dispatch(batch, phrases)
            if accelerator.is_main_process:
                append_records(ordered_records(batch, gathered, failure_record))
                accelerator.print(f"  进度 {counters['written']}/{len(items)}  "
                                  f"已判 {counters['judged']} 失败 {counters['failed']}")
            accelerator.wait_for_everyone()

    active_phrases = dict(phrases_by_root)
    if args.probe_per_caption > 0:
        probe_items = [item for group in work.values() for item in group[:args.probe_per_caption]]
        collected: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for start in range(0, len(probe_items), process_batch_size):
            batch = probe_items[start:start + process_batch_size]
            gathered = dispatch(batch, active_phrases)
            if accelerator.is_main_process:
                for record in ordered_records(batch, gathered, failure_record):
                    if not record.get("judgment_failed"):
                        collected[(record["image_name"], record["caption"])].append(record)
            accelerator.wait_for_everyone()

        if accelerator.is_main_process:
            for key, phrases in phrases_by_root.items():
                kept, pruned = ratio_prune(
                    collected.get(key, ()), phrases,
                    args.constant_yes_ratio, args.constant_no_ratio, args.probe_min_instances,
                )
                active_phrases[key] = tuple(kept)
                if pruned:
                    constant_pairs[f"{key[0]}\t{key[1]}"] = list(pruned)
            for key, records in collected.items():
                append_records(records, active_phrases.get(key, ()))
            judged_ids = {record["instance_id"] for records in collected.values() for record in records}
        else:
            judged_ids = set()
        state = broadcast_object(accelerator, {
            "active_phrases": active_phrases if accelerator.is_main_process else None,
            "judged_ids": sorted(judged_ids) if accelerator.is_main_process else None,
        })
        active_phrases = state["active_phrases"]
        judged_ids = set(state["judged_ids"])
        remaining = [item for item in items if item["instance_id"] not in judged_ids]
        process_batches(remaining, active_phrases)
    else:
        process_batches(items, active_phrases)

    if accelerator.is_main_process:
        if constant_pairs:
            write_json_atomic(args.constant_output, {
                "yes_ratio_threshold": args.constant_yes_ratio,
                "no_ratio_threshold": args.constant_no_ratio,
                "probe_per_caption": args.probe_per_caption,
                "pairs": constant_pairs,
            })
        accelerator.print(
            f"✅ 判断完成：已判 {counters['judged']}，失败 {counters['failed']}；结果 → {args.output}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    for name in ("instances", "candidates"):
        if not getattr(args, name).is_file():
            print(f"❌ 找不到 {name}：{getattr(args, name)}", file=sys.stderr)
            return 1
    if args.batch_size < 1 or args.max_new_tokens < 1 or args.probe_per_caption < 0:
        print("❌ batch-size/max-new-tokens/probe-per-caption 参数无效", file=sys.stderr)
        return 1
    try:
        return run(args)
    except KeyboardInterrupt:
        print("已中断；结果已按条落盘，重跑会自动续接", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 本地判断失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
