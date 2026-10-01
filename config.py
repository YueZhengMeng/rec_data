import asyncio
import json
import os

CONFIG = {
    "api_key": os.getenv("DASHSCOPE_API_KEY"),
    "base_url": "https://batch.dashscope.aliyuncs.com/compatible-mode/v1",
    "model_name": "qwen3.8-flash",
    "image_dir": "F:/REC-8K/rec-8k/",
    "llm_concurrency_limit": 100,
    "llm_max_retries": 3,
    "process_batch_size": 100,
    "crop": {
        "neighbor_k": 5,
        "min_neighbors": 3,
        "scale_factor": 1.5,
        "fine_min_ratio": 0.08,
        "fine_max_ratio": 0.30,
        "fine_min_pixels": 32,
        "sparse_fallback_ratio": 0.25,
        "context_multiplier": 2.0,
        "output_size": 512,
        "marker_color": (255, 32, 32, 235),
        "marker_halo_color": (255, 255, 255, 245),
        "full_marker_radius_ratio": 0.02,
        "full_marker_radius_min": 18,
        "full_marker_radius_max": 72,
    },
}

# 本地 Qwen3.5 推理配置；在线 API 配置仍由上面的 CONFIG 保持不变。
LOCAL_CONFIG = {
    "model_path": "F:/model/Qwen3.5-0.8B",
    "base_model_path": "F:/model/Qwen3.5-0.8B",
    "adapter_path": None,
    "model_mode": "nf4",
    "image_dir": CONFIG["image_dir"],
    "device": "cuda",
    "enable_thinking": False,
    "max_new_tokens": 1024,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repetition_penalty": 1.0,
    # 生成时逐步累计置信度，默认只保存平均值和PPL，减少结果文件大小。
    "record_token_confidences": False,
    # 每张 GPU 的 batch size；多卡等效 batch size = batch_size × GPU进程数。
    "batch_size": 1,
    "result_file": os.path.join(os.path.dirname(__file__), "exp", "exp_local_qwen35_2", "result.json"),
}

EXP_PATH_PREFIX = "exp"

#  实验名称
EXP_NAME = f"exp_all"
# 获取当前时间，精确到秒
# EXP_NAME = time.strftime("%Y%m%d%H%M%S", time.localtime())

# 创建实验文件夹
EXP_SAVE_DIR = os.path.join(os.path.dirname(__file__), EXP_PATH_PREFIX, EXP_NAME)
os.makedirs(EXP_SAVE_DIR, exist_ok=True)

# 创建实验结果文件
EXP_RESULT_FILE = os.path.join(EXP_SAVE_DIR, "result.json")

# 如果EXP_RESULT_FILE不存在，则创建一个空的列表
if not os.path.exists(EXP_RESULT_FILE):
    with open(EXP_RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

# 创建实验结果文件互斥锁，避免多个协程同时写入
EXP_RESULT_FILE_LOCK = asyncio.Lock()

# 创建LLM接口并行信号量
LLM_CONCURRENCY_SEM = asyncio.Semaphore(CONFIG["llm_concurrency_limit"])
