"""使用本地 Qwen3.5-0.8B 提取图片中点标注实例属性。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from accelerate import Accelerator
from accelerate.utils import gather_object

# Windows 默认 GBK 控制台无法输出项目中使用的状态图标，统一切换为 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from old.agents_local import LocalAttributesExtractor
from config import CONFIG, LOCAL_CONFIG
from crop import AdaptiveCropper, CropConfig

REQUIRED_ATTRIBUTE_KEYS = ["appearance", "action", "location", "relation"]


def round_floats(obj, ndigits=4):
    if isinstance(obj, float):
        return round(obj, ndigits)
    elif isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [round_floats(v, ndigits) for v in obj]
    else:
        return obj


def extract_json_candidate(content: str) -> tuple[str | None, str, str | None]:
    """提取裸 JSON 或单个 fenced JSON，不执行任何内容修复。"""
    if not isinstance(content, str) or not content.strip():
        return None, "none", "模型输出为空"

    # 允许 ```json 和 ``` json，代码块内必须由 json.loads 进行严格解析。
    fenced_matches = list(re.finditer(
        r"```\s*json\s*\n?(.*?)\n?\s*```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    if fenced_matches:
        if len(fenced_matches) != 1:
            return None, "json_code_block", "模型输出包含多个 JSON 代码块"
        return fenced_matches[0].group(1).strip(), "json_code_block", None

    # 没有代码块时，只允许整个输出本身就是 JSON；不从自然语言中扫描子串。
    return content.strip(), "raw_text", None


def parse_attributes(content: str) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """严格解析裸 JSON 或 fenced JSON，并返回解析来源和候选文本。"""
    candidate, source, extraction_error = extract_json_candidate(content)
    if extraction_error is not None:
        return None, extraction_error, source, candidate

    try:
        attributes = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as error:
        return None, str(error), source, candidate

    if not isinstance(attributes, dict):
        return None, "JSON 顶层对象不是 dict", source, candidate
    if not set(attributes).intersection(REQUIRED_ATTRIBUTE_KEYS):
        return None, "JSON 至少需要包含四个属性键中的一个", source, candidate
    if any(key not in REQUIRED_ATTRIBUTE_KEYS for key in attributes):
        return None, "JSON 包含未知属性键", source, candidate
    if any(not isinstance(value, list) for value in attributes.values()):
        return None, "JSON 属性值不是 list", source, candidate
    if any(
            not all(isinstance(item, str) for item in value)
            for value in attributes.values()
    ):
        return None, "JSON 属性数组必须只包含字符串", source, candidate
    return attributes, None, source, candidate


def _common_result_fields(instance_id: int, generated: dict[str, Any]) -> dict[str, Any]:
    """提取成功和失败结果共有的模型输出字段。"""
    return {
        "instance_id": instance_id,
        "content": generated.get("content", ""),
        "reasoning_content": generated.get("reasoning_content", ""),
        "input_tokens": generated.get("input_tokens"),
        "output_tokens": generated.get("output_tokens"),
        "output_token_ids": generated.get("output_token_ids", []),
        "token_confidences": generated.get("token_confidences", []),
        "average_confidence": generated.get("average_confidence"),
        "ppl": generated.get("ppl"),
    }


def build_failure_result(
        instance_id: int,
        generated: dict[str, Any] | None,
        status: str,
        error: str,
) -> dict[str, Any]:
    """记录生成失败或 JSON 解析失败，便于后续交给教师模型处理。"""
    result = _common_result_fields(instance_id, generated or {})
    result.update({
        "status": status,
        "generation_failed": status == "generation_failed",
        "json_parse_failed": status == "json_parse_error",
        "attributes": None,
        "error": error,
    })
    return result


def build_success_result(
        instance_id: int,
        generated: dict[str, Any],
        attributes: dict[str, Any],
) -> dict[str, Any]:
    """把严格解析后的属性和模型质量指标保存到结果中。"""
    result = _common_result_fields(instance_id, generated)
    result.update({
        "status": "success",
        "generation_failed": False,
        "json_parse_failed": False,
        "attributes": attributes,
        "error": None,
    })
    return result


def get_process_batch(
        global_batch: list[dict[str, Any]],
        batch_size: int,
        process_index: int,
) -> list[dict[str, Any]]:
    """从全局 batch 中取出当前进程负责的单卡 batch。"""
    start = process_index * batch_size
    end = start + batch_size
    return global_batch[start:end]


def process_local_batch(
        instance_batch: list[dict[str, Any]],
        extractor: LocalAttributesExtractor,
) -> list[dict[str, Any]]:
    """处理单张 GPU 的 batch，并为每个实例返回成功或失败结果。"""
    if not instance_batch:
        return []

    generated_results = []
    batch_error_message = None
    # 批量失败时退回单条推理，避免一条坏图片丢弃整个 batch。
    try:
        generated_results = extractor.infer_batch(instance_batch)
    except Exception as batch_error:
        batch_error_message = str(batch_error)

    generated_result_map = {
        item["instance_id"]: item
        for item in generated_results
        if isinstance(item, dict) and "instance_id" in item
    }
    local_results = []

    # 批量结果缺失时也按单条重试，确保每个实例最终都有状态。
    for instance_data in instance_batch:
        instance_id = instance_data["instance_id"]
        generated = generated_result_map.get(instance_id)
        if generated is None:
            try:
                single_results = extractor.infer_batch([instance_data])
                generated = single_results[0] if single_results else None
            except Exception as item_error:
                local_results.append(build_failure_result(
                    instance_id,
                    None,
                    "generation_failed",
                    str(item_error),
                ))
                continue

        if not isinstance(generated, dict):
            error = "模型未返回推理结果"
            if batch_error_message is not None:
                error += f"；batch推理错误: {batch_error_message}"
            local_results.append(build_failure_result(
                instance_id,
                None,
                "generation_failed",
                error,
            ))
            continue

        content = generated.get("content", "")
        if generated.get("generation_failed") or not isinstance(content, str) or not content.strip():
            local_results.append(build_failure_result(
                instance_id,
                generated,
                "generation_failed",
                "模型未生成有效文本",
            ))
            continue

        attributes, parse_error, _, _ = parse_attributes(content)
        if parse_error is not None:
            failed_result = build_failure_result(
                instance_id,
                generated,
                "json_parse_error",
                parse_error,
            )
            local_results.append(failed_result)
            continue

        local_results.append(build_success_result(instance_id, generated, attributes))

    return local_results


def infer_instances(args: argparse.Namespace) -> None:
    # 创建 HuggingFace Accelerator；每个进程自动对应一张 GPU。
    accelerator = Accelerator(mixed_precision="bf16")

    instance_all = []
    # 加载实例数据；裁剪密度索引必须使用完整数据集。
    instances_path = Path(args.instances)
    if instances_path.exists():
        with instances_path.open("r", encoding="utf-8") as f:
            instance_all = json.load(f)
    if not isinstance(instance_all, list) or not instance_all:
        raise ValueError(f"实例标注为空或格式错误: {args.instances}")

    crop_config = CropConfig(**CONFIG["crop"])
    cropper = AdaptiveCropper.from_instances(instance_all, crop_config)
    accelerator.print(
        f"✅ Crop索引构建完成：{cropper.group_count}组，"
        f"{cropper.unique_point_count}个唯一点，"
        f"忽略{cropper.duplicate_point_count}个重复点"
    )

    # limit 只限制本次调用数量，不能影响上面的密度统计。
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须为正整数")
        instance_all = instance_all[:args.limit]

    existing_result = []
    result_path = Path(args.result)
    if result_path.exists():
        with result_path.open("r", encoding="utf-8") as file:
            existing_result = json.load(file)
    if not isinstance(existing_result, list):
        raise ValueError(f"结果文件格式错误，应为列表: {result_path}")

    extracted_id_set = {item["instance_id"] for item in existing_result if "instance_id" in item}
    instance_list = [
        item for item in instance_all
        if item["instance_id"] not in extracted_id_set
    ]
    accelerator.print(f"✅ {len(instance_all) - len(instance_list)} 条数据已完成属性提取")
    accelerator.print(f"⏳ {len(instance_list)} 条数据待提取")
    if not instance_list:
        accelerator.wait_for_everyone()
        return

    extractor = LocalAttributesExtractor(
        model_path=args.model_path,
        image_dir=args.image_dir,
        cropper=cropper,
        accelerator=accelerator,
        model_mode=args.model_mode,
        base_model_path=args.base_model_path,
        adapter_path=args.adapter_path,
        enable_thinking=args.enable_thinking,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        min_p=args.min_p,
        presence_penalty=args.presence_penalty,
        repetition_penalty=args.repetition_penalty,
        record_token_confidences=args.record_token_confidences,
    )

    success_count = 0
    generation_failed_count = 0
    json_error_count = 0
    # batch_size 表示每张 GPU 的 batch size；数据并行后等效 batch 会按进程数扩大。
    batch_size = max(1, args.batch_size)
    global_batch_size = batch_size * accelerator.num_processes
    accelerator.print(
        f"✅ 单机数据并行启动：{accelerator.num_processes}张GPU，"
        f"每卡batch size={batch_size}，等效batch size={global_batch_size}"
    )

    for start in range(0, len(instance_list), global_batch_size):
        global_batch = instance_list[start:start + global_batch_size]
        local_batch = get_process_batch(
            global_batch,
            batch_size,
            accelerator.process_index,
        )
        accelerator.print(
            f"⏳ 处理第 {start + 1}-{start + len(global_batch)} 条，"
            f"共 {len(instance_list)} 条"
        )

        # 每个进程只推理自己的单卡 batch，再汇总所有 GPU 的 Python 结果。
        local_results = process_local_batch(local_batch, extractor)
        gathered_results = gather_object(local_results)

        if accelerator.is_main_process:
            # 按 global_batch 原始顺序整理，避免多进程汇总顺序影响结果文件。
            gathered_result_map = {
                item["instance_id"]: item
                for item in gathered_results
                if isinstance(item, dict) and "instance_id" in item
            }
            batch_results = []
            for instance_data in global_batch:
                instance_id = instance_data["instance_id"]
                result = gathered_result_map.get(instance_id)
                if result is None:
                    result = build_failure_result(
                        instance_id,
                        None,
                        "generation_failed",
                        "多卡进程未返回推理结果",
                    )
                batch_results.append(result)

                status = result["status"]
                if status == "success":
                    success_count += 1
                    print(f"✅ 数据 {instance_id} 提取成功")
                elif status == "generation_failed":
                    generation_failed_count += 1
                    print(f"❌ 数据 {instance_id} 生成失败: {result['error']}")
                else:
                    json_error_count += 1
                    print(f"❌ 数据 {instance_id} JSON解析失败: {result['error']}")

            existing_result.extend(batch_results)
            # 只有主进程保存结果，避免多个 GPU 进程同时写入同一个文件。
            result_path.parent.mkdir(parents=True, exist_ok=True)
            with result_path.open("w", encoding="utf-8") as file:
                json.dump(round_floats(existing_result), file, ensure_ascii=False, indent=4)

        # 每轮保存完成后再进入下一轮，保证所有进程的数据切片始终同步。
        accelerator.wait_for_everyone()

    accelerator.print(
        f"总成功数: {success_count}/{len(instance_list)}，"
        f"生成失败: {generation_failed_count}，"
        f"JSON解析失败: {json_error_count}"
    )
    accelerator.wait_for_everyone()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", default=LOCAL_CONFIG["model_path"])
    parser.add_argument("--base-model-path", default=LOCAL_CONFIG["base_model_path"])
    parser.add_argument("--adapter-path", default=LOCAL_CONFIG["adapter_path"])
    parser.add_argument(
        "--model-mode",
        choices=("base", "nf4", "full", "lora", "qlora"),
        default=LOCAL_CONFIG["model_mode"],
    )
    parser.add_argument("--image-dir", default=LOCAL_CONFIG["image_dir"])
    parser.add_argument("--instances", default="./anno/instance_data.json")
    parser.add_argument("--result", default=LOCAL_CONFIG["result_file"])
    parser.add_argument("--device", default=LOCAL_CONFIG["device"])
    parser.add_argument(
        "--batch-size",
        type=int,
        default=LOCAL_CONFIG["batch_size"],
        help="每张GPU的batch size；等效batch size还会乘以Accelerate进程数",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--enable-thinking",
        action=argparse.BooleanOptionalAction,
        default=LOCAL_CONFIG["enable_thinking"],
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=LOCAL_CONFIG["max_new_tokens"],
    )
    parser.add_argument("--temperature", type=float, default=LOCAL_CONFIG["temperature"])
    parser.add_argument("--top-p", type=float, default=LOCAL_CONFIG["top_p"])
    parser.add_argument("--top-k", type=int, default=LOCAL_CONFIG["top_k"])
    parser.add_argument("--min-p", type=float, default=LOCAL_CONFIG["min_p"])
    parser.add_argument(
        "--presence-penalty",
        type=float,
        default=LOCAL_CONFIG["presence_penalty"],
    )
    parser.add_argument(
        "--repetition-penalty",
        type=float,
        default=LOCAL_CONFIG["repetition_penalty"],
    )
    parser.add_argument(
        "--record-token-confidences",
        action=argparse.BooleanOptionalAction,
        default=LOCAL_CONFIG["record_token_confidences"],
        help="是否保存每个输出token的置信度；默认只保存平均置信度和PPL",
    )
    args = parser.parse_args()
    infer_instances(args)
