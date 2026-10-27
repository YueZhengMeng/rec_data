"""集中配置：路径、在线与本地模型、裁剪、词表、归一化、门禁、审核与可视化。

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


_PROVIDER = str(_env("REC_PROVIDER", "qwen")).strip().lower()
_PROVIDER_DEFAULTS = {
    "qwen": {
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://batch.dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    },
}
if _PROVIDER not in _PROVIDER_DEFAULTS:
    raise ValueError(
        f"REC_PROVIDER 必须是 qwen 或 deepseek，实际为 {_PROVIDER!r}"
    )
_PROVIDER_DEFAULT = _PROVIDER_DEFAULTS[_PROVIDER]
_API_KEY = os.getenv("REC_API_KEY") or os.getenv(_PROVIDER_DEFAULT["api_key_env"])

CONFIG: dict[str, Any] = {
    # ---- 在线多模态端点 ----
    "provider": _PROVIDER,
    "api_key": _API_KEY,
    "base_url": _env("REC_BASE_URL", _PROVIDER_DEFAULT["base_url"]),
    "model_name": _env("REC_MODEL", _PROVIDER_DEFAULT["model"]),
    "image_dir": _env("REC_IMAGE_DIR", "F:/REC-8K/rec-8k/"),

    # ---- 本地 Qwen 后端 ----
    # step3 与 step5 的两个本地入口共用这一套参数，模型与生成设置只有这一处默认值。
    "local": {
        "model_path": _env("REC_LOCAL_MODEL", "F:/model/Qwen3.5-0.8B"),
        "base_model_path": None,  # None 表示与 model_path 相同
        "adapter_path": None,  # lora / qlora 模式必须提供
        "model_mode": "nf4",  # base / nf4 / full / lora / qlora
        "dtype": "bfloat16",  # 计算与权重的浮点类型：bfloat16 / float16 / float32
        "batch_size": 1,  # 每张 GPU 的实例数
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
        # step2 产出：标注词表；vocabulary 是 step2 --split all 的默认输出
        "vocabulary": "anno/attribute_vocabulary_all.json",
        "vocabulary_train": "anno/attribute_vocabulary_train.json",
        "vocabulary_all": "anno/attribute_vocabulary_all.json",
        "vocabulary_md": "anno/attribute_vocabulary_all.md",
        "vocabulary_overrides": "anno/vocabulary_overrides_all.json",
        # step3 产出：开放提取结果，在线与本地各一份
        "extract_result_online": "anno/extract_result.jsonl",
        "extract_result_local": "anno/extract_result_local.jsonl",
        "extract_sample_online": "anno/extract_sample.json",
        "extract_sample_local": "anno/extract_sample_local.json",
        "extract_preview_online": "anno/extract_preview.json",
        "extract_preview_local": "anno/extract_preview_local.json",
        # step4 产出：归一化候选（一行一条，图片名与 caption 为记录字段）
        "predicate_candidates_online": "anno/predicate_candidates.jsonl",
        "predicate_candidates_local": "anno/predicate_candidates_local.jsonl",
        # step5 产出：三值判断
        "predicate_labels_online": "anno/predicate_labels.jsonl",
        "predicate_labels_local": "anno/predicate_labels_local.jsonl",
        # step6 产出：门禁筛选后的子集
        "subsets": "anno/fine_grained_subsets.jsonl",
        "subset_decisions": "anno/subset_decisions_log.jsonl",
        "review_subset_decisions": "anno/review_subset_decisions_log.jsonl",
        # step7 产出：子集可视化
        "output_dir": "show_subsets",
        # step8 产出：人工审核与最终子集
        "review_dir": "review",
        "subsets_final": "anno/fine_grained_subsets_final.jsonl",
        # step9 输入与产出：保留人工合并子集，另存合成文本及检查结果
        "caption_subsets": "anno/final_subsets.jsonl",
        "captions_online": "anno/subset_captions.jsonl",
        "captions_local": "anno/subset_captions_local.jsonl",
        # step10 独立导出；不修改原 annotations/splits
        "annotations_subset": "anno/annotations_subset.json",
        "splits_subset": "anno/splits_subset.json",
    },

    # ---- 在线调用 ----
    "online": {
        "concurrency": 2500,
        "max_retries": 3,
        "timeout": 120,
        # 默认关闭思考；需要时可用 --enable-thinking 显式开启。
        "enable_thinking": False,
        "batch_size": 2500,
        # Qwen 按官方视觉输入的模式默认值；DeepSeek 仅非思考模式显式传 temperature=1。
        "sampling": {
            "qwen": {
                "non_thinking": {
                    "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                    "presence_penalty": 1.5, "repetition_penalty": 1.0,
                },
                "thinking": {
                    "temperature": 0.6, "top_p": 0.95, "top_k": 20,
                    "presence_penalty": 0.0, "repetition_penalty": 1.0,
                },
            },
            "deepseek": {
                # 非思考模式采用官方推荐的数据抽取温度；其他采样参数使用服务端默认。
                "non_thinking": {"temperature": 1.0},
                # 思考模式 temperature 不生效，因此不发送。
                "thinking": {},
            },
        },
    },

    # ---- step3 开放提取的抽样与预览 ----
    "extract": {
        # 每类抽样的实例数；0 表示全量。
        "sample_per_category_online": 0,
        "sample_per_category_local": 0,
        "seed_online": 42,
        "seed_local": 42,
        # 提示词预览最多保存多少种 (类别, 原属性) 组合。
        "max_preview": 100,
    },

    # ---- step9 属性短语/caption 合成 ----
    "caption": {
        "limit": 0,  # 0 表示全量；先截取再排除已完成 ID
        "presence_penalty_local": 0.0,  # 允许合成时原样复用属性词
        "review_port": 8766,  # step9 本机人工语言审核页面
    },

    # ---- step3 / step5 / step9 续跑策略（不影响模型输入，不纳入运行指纹）----
    "resume": {
        # 默认警告后继续；用 --check-fingerprint 可显式启用严格的配置一致性检查。
        "check_fingerprint": False,
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
        # Global 等比例缩放，对齐下游 RandomResize([800], max_size=1333)，小图也放大。
        # 局部裁剪仍基于原图，不损失 Fine/Context 的原始细节。
        "full_short_side": 800,
        "full_max_size": 1333,
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
        # step2 的默认划分：all 生成 val/test 共用的全量词表；train 只统计训练根。
        "split": "all",
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
        "dedup_similarity_threshold": 0.90,
        # 去重后开放短语 → 闭集词表候选匹配。
        "similarity_threshold": 0.80,
        "similarity_margin": 0.02,
        # 仅用于提示同图其他 caption 的属性词与入选候选可能重复，不影响筛选。
        "cross_caption_duplicate_threshold": 0.85,
        "allow_refinement_split": True,
        # 每根待判断谓词的预算，与候选分配顺序共同决定发给 step5 的谓词集合。
        "max_candidates_per_root": 30,
    },

    # ---- 子集门禁 ----
    "gates": {
        # G1：两侧都要够大，互补预测才良定义。
        "min_support": 2,
        "min_complement": 2,
        # G2：仅在 unknown 占父集比例超过上限时拒绝；unknown 不并入 yes/no。
        "max_unknown_ratio": 0.90,
        # G4：同一根内支持集过于接近的谓词只留一个。
        "max_support_jaccard": 0.80,
        # G5：min(yes, no) / n 的下限，砍掉 90% yes 的伪条件。
        "min_balance": 0.10,
        "max_predicates_per_root": 3,
        "max_depth": 1,
        # 是否为通过门禁的谓词生成否定侧兄弟子集。
        "emit_complement": True,
    },

    # ---- 人工审核 ----
    "review": {
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


def online_sampling(provider: str | None = None,
                    enable_thinking: bool | None = None,
                    *, presence_penalty: float | None = None) -> dict[str, Any]:
    """在线请求和续跑指纹使用同一套采样参数；返回副本以免修改集中配置。"""
    settings = CONFIG["online"]
    provider = CONFIG["provider"] if provider is None else provider
    thinking = settings["enable_thinking"] if enable_thinking is None else enable_thinking
    sampling = dict(settings["sampling"][provider]["thinking" if thinking else "non_thinking"])
    if provider == "qwen" and presence_penalty is not None:
        sampling["presence_penalty"] = presence_penalty
    return sampling


def crop_config():
    """构造 :class:`recsub.crop.CropConfig`；延迟导入以免仅用路径时加载 PIL。"""
    from recsub.crop import CropConfig

    return CropConfig(**CONFIG["crop"])
