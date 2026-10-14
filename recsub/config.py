"""集中配置：路径、在线与本地模型、裁剪、词表、归一化、判断与子集门禁。

各步骤脚本的 argparse 默认值一律取自这里，命令行参数只用于覆盖单次运行，因此
调整默认行为改这一个文件即可。少数默认值允许用环境变量覆盖，见各条注释。

所有路径都相对项目根目录（本包所在目录的上一级）解析，所以从任意工作目录运行
步骤脚本都能找到数据。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# recsub/config.py -> recsub/ -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: Any) -> Any:
    value = os.getenv(name)
    return default if value is None or value == "" else value


CONFIG: dict[str, Any] = {
    # ---- 在线多模态端点 ----
    "api_key": os.getenv("DASHSCOPE_API_KEY"),
    "base_url": _env("REC_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "model_name": _env("REC_MODEL", "qwen3.8-flash"),
    "image_dir": _env("REC_IMAGE_DIR", "F:/REC-8K/rec-8k/"),

    # ---- 本地 Qwen 后端 ----
    # step3 与 step5 的两个本地入口共用这一套参数，模型与生成设置只有这一处默认值。
    "local": {
        "model_path": _env("REC_LOCAL_MODEL", "F:/model/Qwen3.5-0.8B"),
        "base_model_path": None,       # None 表示与 model_path 相同
        "adapter_path": None,          # lora / qlora 模式必须提供
        "model_mode": "nf4",           # base / nf4 / full / lora / qlora
        "dtype": "bfloat16",           # 计算与权重的浮点类型：bfloat16 / float16 / float32
        "batch_size": 1,               # 每张 GPU 的实例数
        "enable_thinking": False,
        "max_new_tokens": 2048,
        "do_sample": True,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repetition_penalty": 1.0,
        "record_token_confidences": False,
    },

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
        # step3 产出：开放提取结果，在线与本地各一份
        "extract_result_online": "anno/extract_result_online.jsonl",
        "extract_result_local": "anno/extract_result_local.jsonl",
        "extract_sample_online": "anno/extract_sample_online.json",
        "extract_sample_local": "anno/extract_sample_local.json",
        "extract_preview_online": "anno/extract_preview_online.json",
        "extract_preview_local": "anno/extract_preview_local.json",
        # step4 产出：归一化候选
        "predicate_candidates": "anno/predicate_candidates.json",
        "generated_vocabulary": "anno/vocabulary_generated.json",
        # step5 产出：三值判断，以及探针剪掉的「根 × 恒定条件」
        "predicate_labels": "anno/predicate_labels.jsonl",
        "predicate_labels_local": "anno/predicate_labels_local.jsonl",
        "predicate_constant": "anno/predicate_constant.json",
        "predicate_constant_local": "anno/predicate_constant_local.json",
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
    "online": {
        "concurrency": 100,
        "max_retries": 3,
        "timeout": 1800.0,
        # 判断阶段的 output token 由思维链主导（实测均值 3187），需要时可用
        # --no-enable-thinking 关掉实测。
        "enable_thinking": True,
        "batch_size": 100,
    },

    # ---- step3 开放提取的抽样与预览 ----
    "extract": {
        # 每类抽样的实例数；0 表示全量。
        "sample_per_category_online": 2,
        "sample_per_category_local": 0,
        "seed_online": 13,
        "seed_local": 42,
        # 提示词预览最多保存多少种 (类别, 原属性) 组合。
        "max_preview": 100,
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
        # 局部视图边长：像素越多，小目标的部件越清楚，代价是 token 与显存。
        # 512 更省，适合先用小模型试跑；768 细节更多。
        "fine_output_size": 512,
        "context_output_size": 512,
        # outline：更小的环、更细的描边、更窄的高光、更短的引线，尽量少覆盖判定要看
        # 的像素（标注只给一个点，无法沿物体描边，能做的只是少遮挡）。
        # ring 是另一套标记样式：更大的环、更宽的白色高光、更长的引线。
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
        # 1 = 收录全部观察到的取值。归一化目标集要尽量全：person 的动作词大多只出现
        # 在一个图里，按频次过滤会把 'climbing a wall' 这类合法标签丢掉。
        "min_root_count": 1,
    },

    # ---- 归一化 ----
    "normalize": {
        "embedding_model": _env("REC_EMBEDDING_MODEL", "F:/model/Qwen3-Embedding-0.6B"),
        "embedding_batch_size": 256,
        "embedding_device": None,
        "use_embedding": True,
        # 开放短语两两去重：比匹配闭集词表更保守，避免误删不同属性。
        "dedup_similarity_threshold": 0.85,
        # 去重后开放短语 → 闭集词表候选匹配。
        "similarity_threshold": 0.75,
        "similarity_margin": 0.02,
        # 仅用于提示同图其他 caption 的属性词与入选候选可能重复，不影响筛选。
        "cross_caption_duplicate_threshold": 0.85,
        "allow_refinement_split": True,
        # 每根待判断谓词的预算，与候选分配顺序共同决定发给 step5 的谓词集合。
        "max_candidates_per_root": 30,
        "use_llm_tail": False,
        # LLM 尾部：每次请求带多少条待解析短语，以及同时发多少路请求。
        "llm_tail_batch_size": 40,
        "llm_tail_concurrency": 50,
        # 每个类别最多接受多少条 LLM 新生成的谓词，防止生成词淹没原词表。
        "max_new_predicates_per_category": 120,
    },

    # ---- step5 三值判断 ----
    "judge": {
        # 探针轮：每根先判这么多实例，用来剪掉几乎恒定的条件；0 表示全量判断。
        "probe_per_caption": 0,
        # 探针至少要判这么多实例才敢剪枝，样本不足时宁可多花 token。
        "probe_min_instances": 4,
        "constant_yes_ratio": 0.98,
        "constant_no_ratio": 0.02,
    },

    # ---- 子集门禁 ----
    "gates": {
        # G1：两侧都要够大，互补预测才良定义。
        "min_support": 1,
        "min_complement": 1,
        # G2：判断不了的不进训练。
        "max_unknown_ratio": 0,
        # G4：同一根内支持集过于接近的谓词只留一个。
        "max_support_jaccard": 0.80,
        # G5：min(yes, no) / n 的下限，砍掉 90% yes 的伪条件。
        "min_balance": 0.15,
        "max_predicates_per_root": 3,
        "max_depth": 1,
        # 是否为通过门禁的谓词生成否定侧兄弟子集。
        "emit_complement": True,
    },

    # ---- 人工审核 ----
    "review": {
        "default_reviewer": "r1",
        "host": "127.0.0.1",
        "port": 8765,
    },

    # ---- 可视化 ----
    "visualize": {
        "max_width": 1100,
        "max_height": 850,
        "sidebar_width": 520,
    },
}


def path(key: str) -> Path:
    """按 key 取产物路径，相对项目根目录解析为绝对路径。"""
    raw = Path(CONFIG["paths"][key])
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


def resolve_dir(value: str | Path) -> Path:
    """命令行传入的目录同样相对项目根目录解析。"""
    resolved = Path(value)
    return resolved if resolved.is_absolute() else PROJECT_ROOT / resolved


def crop_config():
    """构造 :class:`recsub.crop.CropConfig`；延迟导入以免仅用路径时加载 PIL。"""
    from recsub.crop import CropConfig

    return CropConfig(**CONFIG["crop"])
