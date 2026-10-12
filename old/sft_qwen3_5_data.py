"""将在线属性提取结果整理为 Qwen3.5 多模态 SFT 数据。"""

import json
import os
from pathlib import Path

import torch
from PIL import Image
from prompts import build_extract_attributes_prompt
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import Dataset

ATTRIBUTE_KEYS = ("appearance", "action", "location", "relation")
SPLIT_NAMES = ("train", "val", "test")


def serialize_attributes(attributes):
    """仅把最终属性作为监督目标，不训练在线模型的思考过程。"""
    if (not isinstance(attributes, dict) or not attributes
            or any(key not in ATTRIBUTE_KEYS for key in attributes)):
        raise ValueError(f"attributes 只能包含 {ATTRIBUTE_KEYS} 中的非空字段子集")
    if any(not isinstance(value, list) or
           any(not isinstance(item, str) for item in value)
           for value in attributes.values()):
        raise ValueError("attributes 的属性值必须是字符串列表")
    return json.dumps({key: attributes[key] for key in ATTRIBUTE_KEYS if key in attributes},
                      ensure_ascii=False)


def load_sft_samples(result_files, instances_file, splits_file, image_dir):
    """用 instance_id 关联标注，再按原始图像划分训练、验证和测试集。"""
    with open(instances_file, "r", encoding="utf-8") as file:
        instances = json.load(file)
    if not isinstance(instances, list):
        raise ValueError("实例标注必须是列表")
    instances_by_id = {}
    for instance in instances:
        instance_id = instance.get("instance_id")
        if not isinstance(instance_id, int) or isinstance(instance_id, bool):
            raise ValueError(f"实例缺少有效的 instance_id: {instance_id}")
        if instance_id in instances_by_id:
            raise ValueError(f"实例标注中存在重复 instance_id: {instance_id}")
        instances_by_id[instance_id] = instance

    with open(splits_file, "r", encoding="utf-8") as file:
        raw_splits = json.load(file)
    if not isinstance(raw_splits, dict) or set(raw_splits) != set(SPLIT_NAMES):
        raise ValueError(f"splits.json 必须恰好包含 {SPLIT_NAMES}")

    pair_to_split = {}
    image_to_split = {}
    for split, pairs in raw_splits.items():
        if not isinstance(pairs, list):
            raise ValueError(f"{split} 划分必须是列表")
        for pair in pairs:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    any(not isinstance(value, str) for value in pair)):
                raise ValueError(f"{split} 包含非法的 [image_name, caption]: {pair}")
            image_name, caption = pair
            key = (image_name, caption)
            if key in pair_to_split:
                raise ValueError(f"划分中存在重复的图像/描述组合: {key}")
            if image_name in image_to_split and image_to_split[image_name] != split:
                raise ValueError(f"同一图像出现在多个划分中: {image_name}")
            pair_to_split[key] = split
            image_to_split[image_name] = split

    if isinstance(result_files, (str, os.PathLike)):
        result_files = [result_files]
    if not result_files:
        raise ValueError("至少需要一个在线 result.json 文件")

    samples = {split: [] for split in SPLIT_NAMES}
    seen_ids = set()
    checked_images = set()
    for result_file in result_files:
        with open(result_file, "r", encoding="utf-8") as file:
            results = json.load(file)
        if not isinstance(results, list):
            raise ValueError(f"{result_file} 的顶层必须是列表")
        for row in results:
            if not isinstance(row, dict):
                raise ValueError(f"{result_file} 包含非对象记录")
            instance_id = row.get("instance_id")
            if not isinstance(instance_id, int) or isinstance(instance_id, bool):
                raise ValueError(f"{result_file} 包含非法 instance_id: {instance_id}")
            if instance_id in seen_ids:
                raise ValueError(f"在线结果存在重复 instance_id: {instance_id}")
            seen_ids.add(instance_id)
            if instance_id not in instances_by_id:
                raise ValueError(f"在线结果找不到对应实例: {instance_id}")
            instance = instances_by_id[instance_id]
            pair = (instance.get("image_name"), instance.get("caption"))
            if pair not in pair_to_split:
                raise ValueError(f"实例 {instance_id} 不在原始 train/val/test 划分中: {pair}")
            if (not isinstance(instance.get("category"), str) or
                    not isinstance(instance.get("point"), (list, tuple)) or
                    len(instance["point"]) != 2):
                raise ValueError(f"实例 {instance_id} 缺少类别或点坐标")
            if pair[0] not in checked_images:
                image_path = Path(image_dir) / pair[0]
                if not image_path.is_file():
                    raise FileNotFoundError(f"实例 {instance_id} 的原始图片不存在: {image_path}")
                try:
                    with Image.open(image_path) as source_image:
                        source_image.verify()
                except OSError as error:
                    raise ValueError(f"实例 {instance_id} 的原始图片无法读取: {image_path}") from error
                checked_images.add(pair[0])
            target = serialize_attributes(row.get("attributes"))
            samples[pair_to_split[pair]].append({"instance": instance, "target": target})

    for split, rows in samples.items():
        if not rows:
            raise ValueError(f"在线结果的 {split} 划分为空；需要每个划分至少一条有效标注")
    return samples, instances


class VLMAttributeDataset(Dataset):
    """按需从原图重建与在线/本地推理一致的三个视图。"""

    def __init__(self, samples, image_dir, cropper):
        self.samples = samples
        self.image_dir = image_dir
        self.cropper = cropper

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        sample = self.samples[index]
        instance = sample["instance"]
        image_path = os.path.join(self.image_dir, instance["image_name"])
        with Image.open(image_path) as source_image:
            image_global = source_image.convert("RGB")
            crops = self.cropper.create_crops(
                image_global,
                image_name=instance["image_name"],
                category=instance["category"],
                point=instance["point"],
            )

        text_prompt = build_extract_attributes_prompt(
            instance["category"], instance.get("attributes", "")
        )
        images = [crops.full_image, crops.context_image, crops.fine_image]
        messages = [{"role": "user", "content": [
                                                    {"type": "image", "image": image} for image in images
                                                ] + [{"type": "text", "text": text_prompt}]}]
        return {"instance_id": instance["instance_id"], "messages": messages,
                "images": images, "target": sample["target"]}


class VLMCompletionCollator:
    """多模态右填充：只计算 assistant 最终 JSON 回答的 causal loss。"""

    def __init__(self, processor, max_length=16384):
        self.processor = processor
        self.max_length = max_length
        self.processor.tokenizer.padding_side = "right"
        if self.processor.tokenizer.pad_token_id is None:
            self.processor.tokenizer.pad_token = self.processor.tokenizer.eos_token
        if self.processor.tokenizer.eos_token != "<|im_end|>":
            raise ValueError("Qwen3.5 assistant 结束符应为 <|im_end|>")

    def __call__(self, features):
        if not features:
            raise ValueError("不能整理空 batch")
        sequences = {"input_ids": [], "attention_mask": [], "labels": []}
        vision = {"pixel_values": [], "image_grid_thw": []}
        token_types = []

        for feature in features:
            # 先让 processor 根据三张真实图片展开 image token；不能用纯文本长度估算 mask。
            if len(feature["images"]) != 3:
                raise ValueError(f"实例 {feature['instance_id']} 必须包含三张图片")
            prompt = self.processor.apply_chat_template(
                feature["messages"], tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = self.processor(text=prompt, images=feature["images"], return_tensors="pt")
            if not all(key in encoded for key in vision):
                raise ValueError("processor 没有返回图片 patch 与 image_grid_thw")
            if encoded["image_grid_thw"].shape[0] != 3:
                raise ValueError(f"实例 {feature['instance_id']} 的图像网格数量不等于 3")
            extra_keys = set(encoded) - {"input_ids", "attention_mask", "mm_token_type_ids",
                                         "pixel_values", "image_grid_thw"}
            if extra_keys:
                raise ValueError(f"尚未支持的多模态 processor 字段: {sorted(extra_keys)}")
            prompt_ids = encoded["input_ids"].squeeze(0)
            answer_ids = self.processor.tokenizer(
                feature["target"] + self.processor.tokenizer.eos_token,
                add_special_tokens=False, return_tensors="pt",
            )["input_ids"].squeeze(0)
            if answer_ids.numel() == 0 or answer_ids[-1].item() != self.processor.tokenizer.eos_token_id:
                raise ValueError(f"实例 {feature['instance_id']} 的回答没有结束符")
            length = prompt_ids.numel() + answer_ids.numel()
            if self.max_length is not None and length > self.max_length:
                raise ValueError(f"实例 {feature['instance_id']} 长度 {length} 超过 max_length="
                                 f"{self.max_length}；请调大限制或调整图片尺寸，不能截断监督内容")

            sequences["input_ids"].append(torch.cat([prompt_ids, answer_ids]))
            sequences["attention_mask"].append(torch.cat([
                encoded["attention_mask"].squeeze(0), torch.ones_like(answer_ids)
            ]))
            sequences["labels"].append(torch.cat([
                torch.full_like(prompt_ids, -100), answer_ids
            ]))
            if "mm_token_type_ids" in encoded:
                token_types.append(torch.cat([
                    encoded["mm_token_type_ids"].squeeze(0), torch.zeros_like(answer_ids)
                ]))
            vision["pixel_values"].append(encoded["pixel_values"])
            vision["image_grid_thw"].append(encoded["image_grid_thw"])

        if token_types and len(token_types) != len(features):
            raise ValueError("processor 对不同样本返回了不一致的 mm_token_type_ids")
        batch = {
            "input_ids": pad_sequence(sequences["input_ids"], batch_first=True,
                                      padding_value=self.processor.tokenizer.pad_token_id),
            "attention_mask": pad_sequence(sequences["attention_mask"], batch_first=True,
                                           padding_value=0),
            "labels": pad_sequence(sequences["labels"], batch_first=True, padding_value=-100),
            "pixel_values": torch.cat(vision["pixel_values"], dim=0),
            "image_grid_thw": torch.cat(vision["image_grid_thw"], dim=0),
        }
        if token_types:
            batch["mm_token_type_ids"] = pad_sequence(token_types, batch_first=True, padding_value=0)
        return batch
