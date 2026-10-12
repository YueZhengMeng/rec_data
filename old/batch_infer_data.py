"""生成可上传到阿里云 Batch API 的多模态 jsonl 请求文件。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from PIL import Image
from agents import build_extract_attributes_messages
from config import CONFIG
from crop import AdaptiveCropper, CropConfig
from tqdm import tqdm
from utils import encode_image

# 阿里云 Batch 的平台硬限制；默认写入时使用更保守的单文件大小。
MAX_PLATFORM_LINE_BYTES = 1 * 1024 * 1024
MAX_PLATFORM_FILE_BYTES = 500 * 1024 * 1024
MAX_PLATFORM_REQUESTS = 50_000
BATCH_ENDPOINT = "/v1/chat/completions"


def load_split_pairs(split_path, split):
    """
        读取 train/val/test 的 image-caption 对
        all 模式不做过滤
    """
    if split == "all":
        return None
    with open(split_path, "r", encoding="utf-8") as file:
        splits = json.load(file)
    return {tuple(item) for item in splits[split]}


def select_points(annotations, max_points, split_pairs, limit=None):
    """
        按原始插入顺序，为每个 image-caption 选择随机最多 max_points 个点。
        image_idx/caption_idx/point_idx 与 data_preprocess.py 保持一致，
        这样 custom_id 可以和现有 instance_data.json 的主键体系对应起来。
    """

    selected_count = 0
    instance_id = 0
    selected_points = []

    for image_idx, (image_name, image_annotations) in enumerate(tqdm(annotations.items())):
        for caption_idx, (caption, record) in enumerate(image_annotations.items()):
            points = record["points"]

            if split_pairs is not None and (image_name, caption) not in split_pairs:
                # 即使该 caption 不属于当前 split，也要推进全局 instance_id。
                instance_id += len(points)
                continue

            # 使用标注文件中的前 max_points 个点
            # for point_idx in range(max_points):

            # 从 points 中随机选取 max_points 个点的索引
            if len(points) < max_points:
                selected_points_index = range(len(points))
            else:
                selected_points_index = random.sample(range(len(points)), max_points)
            for point_idx in selected_points_index:
                selected_points.append(
                    {
                        "image_name": image_name,
                        "caption": caption,
                        "category": record.get("class"),
                        "attributes": record.get("attribute", ""),
                        "type": record.get("type"),
                        "point": points[point_idx],
                        "image_idx": image_idx,
                        "caption_idx": caption_idx,
                        "point_idx": point_idx,
                        # 与 data_preprocess.py 中逐点累加的 instance_id 一致。
                        "instance_id": instance_id + point_idx,
                    }
                )
                selected_count += 1
                if limit is not None and selected_count >= limit:
                    return selected_points
            instance_id += len(points)
    return selected_points


def build_batch_row(instance, image, cropper, model, enable_thinking=True):
    """
        为单个标注点构造一条阿里云 OpenAI-compatible Batch 请求
    """
    image_name = instance["image_name"]
    category = instance["category"]

    # 执行裁剪
    crops = cropper.create_crops(
        image,
        image_name=image_name,
        category=category,
        point=instance["point"],
    )
    base64_global = encode_image(crops.full_image)
    base64_context = encode_image(crops.context_image)
    base64_fine = encode_image(crops.fine_image)
    messages = build_extract_attributes_messages(
        base64_global,
        base64_context,
        base64_fine,
        category,
        instance["attributes"],
    )

    return {
        "custom_id": f"attr-i{instance['image_idx']}-c{instance['caption_idx']}-p{instance['point_idx']}",
        "method": "POST",
        "url": BATCH_ENDPOINT,
        "body": {
            "model": model,
            "enable_thinking": enable_thinking,
            "messages": messages,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--annotations", default="./anno/annotations.json")
    parser.add_argument("--splits", default="./anno/splits.json")
    parser.add_argument("--split", choices=("all", "train", "val", "test"), default="all")
    parser.add_argument("--image-dir", default=CONFIG["image_dir"])
    parser.add_argument("--output-dir", default="./batch_data")
    parser.add_argument("--model", default=CONFIG["model_name"])
    parser.add_argument("--enable-thinking", default=True)
    parser.add_argument("--max-points", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=10000)
    args = parser.parse_args()
    random.seed(args.seed)

    with open(args.annotations, "r", encoding="utf-8") as f:
        annotations = json.load(f)
    split_pairs = load_split_pairs(args.splits, args.split)
    crop_config = CropConfig(**CONFIG["crop"])

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    cropper = AdaptiveCropper.from_annotations(annotations, crop_config)

    selected = select_points(annotations, args.max_points, split_pairs)

    batch_rows = []
    batch_idx = 0

    for instance in tqdm(selected):
        image_path = Path(args.image_dir) / instance["image_name"]

        with Image.open(image_path) as image:
            row = build_batch_row(
                instance, image, cropper, args.model, args.enable_thinking
            )
        row
        batch_rows.append(row)

        if len(batch_rows) == args.batch_size:
            output_file = output_path / f"batch_data_{batch_idx:03d}.jsonl"

            with open(output_file, "w", encoding="utf-8") as f:
                f.writelines(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for row in batch_rows
                )

            batch_rows.clear()
            batch_idx += 1

    # 保存最后一个不满 batch_size 的文件
    if batch_rows:
        output_file = output_path / f"batch_data_{batch_idx:03d}.jsonl"

        with open(output_file, "w", encoding="utf-8") as f:
            f.writelines(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in batch_rows
            )

        print(f"最后一个 batch：{len(batch_rows)} 条")
