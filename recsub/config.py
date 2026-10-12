"""集中配置：路径、在线模型、裁剪、词表、归一化与子集门禁。

所有路径都相对项目根目录（本包所在目录的上一级）解析，因此从任意工作目录
运行步骤脚本都能找到数据。环境变量在导入时覆盖默认值，命令行参数再由各
步骤脚本调用 :func:`apply_overrides` 覆盖。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping

# recsub/config.py -> recsub/ -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: Any) -> Any:
    value = os.getenv(name)
    return default if value is None or value == "" else value


CONFIG: dict[str, Any] = {
    # ---- 在线多模态模型 ----
    # 默认走 OpenAI 兼容端点；如需 DashScope 的批量端点，用 REC_BASE_URL 覆盖。
    "api_key": os.getenv("DASHSCOPE_API_KEY"),
    "base_url": _env("REC_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "model_name": _env("REC_MODEL", "qwen3.8-flash"),
    "image_dir": _env("REC_IMAGE_DIR", "F:/REC-8K/rec-8k/"),

    # ---- 产物路径（相对项目根目录）----
    "paths": {
        # 输入
        "annotations": "anno/annotations.json",
        "splits": "anno/splits.json",
        # step1 产出：实例表
        "instance_data": "anno/instance_data.jsonl",
        # step2 产出：标注词表
        "vocabulary": "anno/attribute_vocabulary.json",
        "vocabulary_md": "anno/attribute_vocabulary.md",
        "vocabulary_overrides": "anno/vocabulary_overrides.json",
        # step3 产出：引导式开放提取
        "extract_result": "anno/extract_result.jsonl",
        "extract_sample": "anno/extract_sample_ids.json",
        # step4 产出：归一化候选
        "predicate_candidates": "anno/predicate_candidates.json",
        "generated_vocabulary": "anno/vocabulary_generated.json",
        # step5 产出：三值判断
        "predicate_labels": "anno/predicate_labels.jsonl",
        # step6 产出：门禁筛选后的子集
        "subsets": "anno/fine_grained_subsets.jsonl",
        "subset_decisions": "anno/subset_decisions_log.jsonl",
        # step7 产出：人工审核与最终子集
        "review_dir": "review",
        "subsets_final": "anno/fine_grained_subsets_final.jsonl",
        # step8 产出：可视化
        "output_dir": "show_subsets",
    },

    # ---- 在线调用 ----
    "llm": {
        "concurrency": 100,
        "max_retries": 3,
        "timeout": 1800.0,
        # 判断步骤的 output token 由思维链主导（实测均值 3187），需要时关掉实测。
        "enable_thinking": True,
        "batch_size": 100,
    },

    # ---- 裁剪三视图 ----
    "crop": {
        "neighbor_k": 5,
        "min_neighbors": 3,
        "scale_factor": 1.5,
        "fine_min_ratio": 0.08,
        "fine_max_ratio": 0.30,
        "fine_min_pixels": 32,
        "sparse_fallback_ratio": 0.25,
        "context_multiplier": 2.0,
        # 局部视图分辨率从 512 提到 768：密集场景下小目标的部件需要更多像素。
        "fine_output_size": 768,
        "context_output_size": 768,
        # outline：环更小、描边更细、高光收窄到与描边同宽、引线更短，
        # 尽量少覆盖判定要看的像素（标注只有一个点，无法真的沿物体描边）。
        # ring 保留原行为（宽白高光 + 长引线），便于与旧结果对比。
        "marker_style": "outline",
        "outline_radius_ratio": 0.014,
        "marker_color": (255, 32, 32, 235),
        "marker_halo_color": (255, 255, 255, 245),
        "full_marker_radius_ratio": 0.02,
        "full_marker_radius_min": 18,
        "full_marker_radius_max": 72,
    },

    # ---- 词表 ----
    "vocabulary": {
        # 只从 train 划分统计，避免 val/test 的标注词进入提示词。
        "split": "train",
        # 1 = 收录全部观察到的取值。归一化目标集要尽量全：person 的动作词大多
        # 只出现在一个图里，按频次过滤会把 'climbing a wall' 这类合法标签丢掉。
        # 「长尾噪声不该进 few-shot」由 fewshot() 按频次与 scope 处理，不靠这里。
        "min_root_count": 1,
        "max_fewshot_per_dimension": 8,
        "max_fewshot_total": 24,
    },

    # ---- 归一化 ----
    "normalize": {
        "embedding_model": _env("REC_EMBEDDING_MODEL", "F:/model/Qwen3-Embedding-0.6B"),
        "embedding_batch_size": 64,
        "embedding_device": None,
        "similarity_threshold": 0.86,
        "use_llm_tail": True,
        "llm_tail_batch_size": 40,
    },

    # ---- 子集门禁 ----
    "gates": {
        # G1：两侧都要够大，互补预测才良定义。
        "min_support": 5,
        "min_complement": 5,
        # G2：判断不了的不进训练。
        "max_unknown_ratio": 0.10,
        # G4：同一根内支持集过于接近的谓词只留一个。
        "max_support_jaccard": 0.80,
        # G5：min(yes, no) / n 的下限，砍掉 90% yes 的伪条件。
        "min_balance": 0.15,
        "max_predicates_per_root": 3,
        "max_depth": 1,
        # 是否为通过门禁的谓词生成否定侧兄弟子集（对应原数据里的 not X 根）。
        "emit_complement": True,
        # step5 提前停止：几乎恒定的谓词无需继续判断。
        "constant_yes_ratio": 0.98,
        "constant_no_ratio": 0.02,
    },

    # ---- 人工审核 ----
    "review": {
        "default_reviewer": "r1",
        "shard_count": 4,
        "host": "127.0.0.1",
        "port": 8765,
    },
}


def path(key: str, config: Mapping[str, Any] | None = None) -> Path:
    """按 key 取产物路径，相对项目根目录解析为绝对路径。"""
    config = config or CONFIG
    raw = Path(config["paths"][key])
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


def resolve_dir(value: str | Path) -> Path:
    """命令行传入的目录同样相对项目根目录解析。"""
    resolved = Path(value)
    return resolved if resolved.is_absolute() else PROJECT_ROOT / resolved


def crop_config(config: Mapping[str, Any] | None = None):
    """构造 :class:`recsub.crop.CropConfig`；延迟导入以免仅用路径时加载 PIL。"""
    from recsub.crop import CropConfig

    config = config or CONFIG
    crop_settings = dict(config["crop"])
    # 兼容旧键：output_size 同时作为 fine / context 的回退值。
    legacy_size = crop_settings.pop("output_size", None)
    if legacy_size is not None:
        crop_settings.setdefault("fine_output_size", legacy_size)
        crop_settings.setdefault("context_output_size", legacy_size)
    return CropConfig(**crop_settings)


def apply_overrides(config: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """按段落深合并命令行覆盖项，返回新配置（不修改输入的 CONFIG）。"""
    merged = copy.deepcopy(config)
    for section, values in overrides.items():
        if values is None:
            continue
        if isinstance(values, Mapping) and isinstance(merged.get(section), Mapping):
            merged[section] = {**merged[section], **values}
        else:
            merged[section] = values
    return merged


def require_api_key(config: Mapping[str, Any] | None = None) -> str:
    config = config or CONFIG
    api_key = config.get("api_key")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY，无法调用在线模型")
    return api_key
