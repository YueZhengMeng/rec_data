"""本地推理的 Accelerate 辅助：单卡模型副本、数据并行与结果按 rank 汇总。"""

from __future__ import annotations

from typing import Any

import torch


def create_accelerator():
    """Create the bf16 Accelerator used by both local pipeline steps."""
    try:
        from accelerate import Accelerator
    except ImportError as error:  # pragma: no cover - exercised in real launch
        raise RuntimeError(
            "本地推理需要 accelerate、torch、transformers 和 peft"
        ) from error
    return Accelerator(mixed_precision="bf16")


def validate_device(accelerator) -> None:
    """检查本 rank 的 CUDA/bf16，不静默降级或跨卡加载。"""
    device = accelerator.device
    if device.type != "cuda" or not torch.cuda.is_available():
        raise RuntimeError(f"本地 bf16/NF4 推理需要 CUDA；当前 device={device}")
    device_index = device.index if device.index is not None else torch.cuda.current_device()
    if device_index != accelerator.local_process_index:
        raise RuntimeError(
            f"rank 设备不匹配：device={device}, local_rank={accelerator.local_process_index}"
        )
    if accelerator.mixed_precision != "bf16":
        raise RuntimeError("本地推理需要 Accelerator(mixed_precision='bf16')")
    distributed_type = getattr(accelerator, "distributed_type", "NO")
    if distributed_type not in ("NO", "MULTI_GPU"):
        raise RuntimeError("本地推理仅支持单卡或 Accelerate MULTI_GPU 数据并行，不支持 FSDP/DeepSpeed")
    with torch.cuda.device(device):
        if not torch.cuda.is_bf16_supported(including_emulation=False):
            raise RuntimeError(f"{device} 不支持原生 bf16；请使用支持 bf16 的 CUDA GPU")


def validate_runtime(accelerator, per_rank_batch_size: int) -> None:
    """Validate and report rank/device semantics before loading a model.

    The local scripts deliberately implement inference data parallelism themselves:
    each rank owns one model replica and ``per_rank_batch_size`` examples.  The
    model must not be passed to ``Accelerator.prepare``/DDP afterwards.
    """
    if per_rank_batch_size < 1:
        raise ValueError("batch-size 必须 ≥1")

    device = getattr(accelerator, "device", None)
    num_processes = int(getattr(accelerator, "num_processes", 1))
    process_index = int(getattr(accelerator, "process_index", 0))
    local_index = int(getattr(accelerator, "local_process_index", process_index))
    mixed_precision = getattr(accelerator, "mixed_precision", "bf16")

    if num_processes < 1 or not 0 <= process_index < num_processes:
        raise RuntimeError(
            f"Accelerate 进程状态无效：rank={process_index}, processes={num_processes}"
        )
    validate_device(accelerator)

    print(
        f"✅ Accelerate rank {process_index}/{num_processes}："
        f"local_rank={local_index}, device={device}, mixed_precision={mixed_precision}, "
        f"每卡 batch={per_rank_batch_size}，全局 batch={per_rank_batch_size * num_processes}"
    )


def ordered_records(batch: list[dict], gathered: list[dict], failure_factory) -> list[dict]:
    """Restore batch order and synthesize one failure row per missing ID."""
    expected = {item["instance_id"] for item in batch}
    by_id: dict[Any, dict] = {}
    duplicate_ids: set[Any] = set()
    for record in gathered:
        if not isinstance(record, dict) or "instance_id" not in record:
            continue
        instance_id = record["instance_id"]
        if instance_id in by_id:
            duplicate_ids.add(instance_id)
        by_id[instance_id] = record

    result: list[dict] = []
    for item in batch:
        instance_id = item["instance_id"]
        record = by_id.get(instance_id)
        if record is None:
            record = failure_factory(item, "多卡进程未返回推理结果")
        elif instance_id in duplicate_ids:
            record = failure_factory(item, "多卡进程返回重复实例结果")
        result.append(record)
    unexpected = set(by_id) - expected
    if unexpected:
        raise RuntimeError(f"多卡进程返回了不属于当前 batch 的实例：{sorted(unexpected)[:5]}")
    return result
