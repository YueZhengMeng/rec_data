"""step3 本地入口：与在线入口同一套提示词，改走本地 Qwen 的 processor 推理。

每个 Accelerate rank 在一张 GPU 上加载一份完整模型副本，`--batch-size` 是每张
GPU 的 batch size，rank 之间只通过 ``gather_object`` 汇总结果，只有主进程写文件。

    python step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 2
    accelerate launch step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from recsub.config import CONFIG, crop_config, path
from recsub.console import configure_console
from recsub.crop import AdaptiveCropper
from recsub.extract import (
    completed_ids, extraction_record, file_digest, iter_selected, parse_attributes,
    run_fingerprint, select_work,
)
from recsub.io import ensure_parent, iter_jsonl, write_json_atomic
from recsub.local_runtime import create_accelerator, ordered_records, validate_runtime
from recsub.prompts import EXTRACT_ATTRIBUTES_PROMPT, build_extract_attributes_prompt


def fingerprint_for(args: argparse.Namespace, world_size: int = 1) -> str:
    return run_fingerprint({
        "backend": "local", "instances_sha256": file_digest(args.instances),
        "image_dir": str(Path(args.image_dir).resolve()),
        "model_path": str(Path(args.model_path).resolve()),
        "base_model_path": str(Path(args.base_model_path or args.model_path).resolve()),
        "adapter_path": str(Path(args.adapter_path).resolve()) if args.adapter_path else None,
        "mode": args.model_mode, "dtype": args.dtype,
        "enable_thinking": args.enable_thinking,
        "max_new_tokens": args.max_new_tokens, "do_sample": args.do_sample,
        "temperature": args.temperature, "top_p": args.top_p, "top_k": args.top_k,
        "min_p": args.min_p, "presence_penalty": args.presence_penalty,
        "repetition_penalty": args.repetition_penalty,
        "world_size": world_size,
        "batch_size_per_rank": args.batch_size,
        "crop": asdict(crop_config()), "prompt": EXTRACT_ATTRIBUTES_PROMPT,
    })


def get_process_batch(batch: list[dict], batch_size: int, process_index: int) -> list[dict]:
    return batch[process_index * batch_size:(process_index + 1) * batch_size]


def process_local_batch(instances: list[dict], extractor) -> list[dict]:
    """批失败时单条重试；每个实例都生成成功或可续跑的失败记录。"""
    if not instances:
        return []
    try:
        generated = extractor.infer_batch(instances)
    except Exception:
        generated = []
    responses = {item["instance_id"]: item for item in generated
                 if isinstance(item, Mapping) and "instance_id" in item}
    records = []
    for instance in instances:
        response = responses.get(instance["instance_id"])
        if response is None:
            try:
                single = extractor.infer_batch([instance])
                response = single[0] if single else None
            except Exception as error:
                records.append(extraction_record(instance, reason=f"本地生成失败: {error}"))
                continue
        if not isinstance(response, Mapping):
            records.append(extraction_record(instance, reason="未返回推理结果"))
            continue
        usage = {key: response[key] for key in (
            "input_tokens", "output_tokens", "average_confidence", "ppl",
            "output_token_ids", "token_confidences"
        ) if key in response and (key not in ("output_token_ids", "token_confidences")
                                  or response[key])}
        content = response.get("content")
        if response.get("generation_failed") or not isinstance(content, str) or not content.strip():
            records.append(extraction_record(instance, usage=usage,
                                             reason="模型未生成有效文本"))
            continue
        records.append(extraction_record(
            instance, attributes=parse_attributes(content), usage=usage, reason="输出解析失败",
        ))
    return records


def append_batch(output: Path, records: list[dict]) -> None:
    ensure_parent(output)
    with output.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(args: argparse.Namespace, *, accelerator=None, extractor_factory=None) -> int:
    if accelerator is None:
        accelerator = create_accelerator()
    validate_runtime(accelerator, args.batch_size)
    if extractor_factory is None:
        from recsub.llm_local import LocalAttributesExtractor
        extractor_factory = LocalAttributesExtractor

    fingerprint = fingerprint_for(args, accelerator.num_processes)
    # 只有写文件的主进程认领输出路径，避免各 rank 争用同一个标记文件。
    completed = completed_ids(args.output, fingerprint, claim=accelerator.is_main_process)
    image_names = ({name.strip() for name in args.image_names.split(",") if name.strip()}
                   if args.image_names else None)
    selected, stats = select_work(
        args.instances, image_names=image_names, sample_per_category=args.sample_per_category,
        seed=args.seed, completed=completed,
        sample_output=(args.sample_output if accelerator.is_main_process and args.sample_per_category > 0
                       else None),
    )
    accelerator.wait_for_everyone()
    if not selected:
        accelerator.print("没有需要处理的实例")
        return 0

    cropper = AdaptiveCropper.from_instances(iter_jsonl(args.instances), crop_config())
    items = list(iter_selected(args.instances, selected))
    accelerator.print(f"✅ Crop 索引 {cropper.group_count} 组，待处理 {len(items)} 个实例")
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
    if accelerator.is_main_process and args.max_preview:
        preview = {}
        for instance in items:
            key = (str(instance["category"]), str(instance["attributes"]))
            preview[json.dumps(key, ensure_ascii=False)] = {
                "category": key[0], "known_attributes": key[1],
                "prompt": build_extract_attributes_prompt(*key),
            }
            if len(preview) >= args.max_preview:
                break
        write_json_atomic(args.prompt_preview, preview)

    from accelerate.utils import gather_object
    batch_size = args.batch_size * accelerator.num_processes
    succeeded = failed = 0
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        local = get_process_batch(batch, args.batch_size, accelerator.process_index)
        responses = gather_object(process_local_batch(local, extractor))
        if accelerator.is_main_process:
            ordered = ordered_records(
                batch, responses,
                lambda item, reason: extraction_record(item, reason=reason),
            )
            append_batch(args.output, ordered)
            succeeded += sum(not record.get("failed") for record in ordered)
            failed += sum(bool(record.get("failed")) for record in ordered)
            accelerator.print(f"  进度 {start + len(batch)}/{len(items)}  成功 {succeeded} 失败 {failed}")
        accelerator.wait_for_everyone()
    accelerator.print(f"✅ 结果 → {args.output}；成功 {succeeded} 失败 {failed}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地 Qwen 开放属性提取")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--image-dir", default=CONFIG["image_dir"])
    parser.add_argument("--output", type=Path, default=path("extract_result_local"))
    parser.add_argument("--sample-output", type=Path, default=path("extract_sample_local"))
    parser.add_argument("--prompt-preview", type=Path, default=path("extract_preview_local"))
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--sample-per-category", type=int,
                        default=CONFIG["extract"]["sample_per_category_local"],
                        help="每类抽样个数；0 表示全量")
    parser.add_argument("--seed", type=int, default=CONFIG["extract"]["seed_local"])
    parser.add_argument("--batch-size", type=int, default=CONFIG["local"]["batch_size"],
                        help="每张 GPU 的实例数")
    parser.add_argument("--max-preview", type=int, default=CONFIG["extract"]["max_preview"])
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if not args.instances.is_file():
        print(f"❌ 找不到实例表：{args.instances}", file=sys.stderr)
        return 1
    if args.batch_size < 1 or args.max_new_tokens < 1 or args.sample_per_category < 0:
        print("❌ batch-size/max-new-tokens 必须 ≥1；抽样数必须 ≥0", file=sys.stderr)
        return 1
    try:
        return run(args)
    except KeyboardInterrupt:
        print("已中断；结果已按批追加", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 本地提取失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
