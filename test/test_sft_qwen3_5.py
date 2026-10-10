"""Qwen3.5 实例属性 SFT 的数据关联、图像 batch 与 loss 测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch
from PIL import Image

from crop import AdaptiveCropper, CropConfig
from old.finetune_Qwen3_5 import (completion_loss_inputs, completion_token_count,
                                  mean_token_loss, save_best, select_lora_modules)
from old.sft_qwen3_5_data import (VLMAttributeDataset, VLMCompletionCollator,
                                  load_sft_samples, serialize_attributes)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = Path("F:/model/Qwen3.5-0.8B")
ATTRIBUTES = {"appearance": ["bright orange"], "action": ["standing"],
              "location": [], "relation": ["beside a box"]}


class TestSFTData(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.images = self.root / "images"
        self.images.mkdir()
        self.instances = []
        self.splits = {"train": [], "val": [], "test": []}
        for index, split in enumerate(self.splits):
            image_name = f"{split}.jpg"
            Image.new("RGB", (80, 80), (index * 50, 80, 140)).save(self.images / image_name)
            self.instances.append({"instance_id": index, "image_name": image_name,
                                   "caption": "SECRET_CAPTION", "category": "pen",
                                   "attributes": "red", "point": [40, 40]})
            self.splits[split].append([image_name, "SECRET_CAPTION"])
        self.results = [{"instance_id": index, "attributes": ATTRIBUTES,
                         "reasoning_content": "LEAKY_REASON"} for index in range(3)]
        self._save_files()

    def _save_files(self):
        for name, data in (("instances.json", self.instances), ("splits.json", self.splits),
                           ("result.json", self.results)):
            with open(self.root / name, "w", encoding="utf-8") as file:
                json.dump(data, file)

    def load(self):
        return load_sft_samples([self.root / "result.json"], self.root / "instances.json",
                                self.root / "splits.json", self.images)

    def test_join_and_target_order(self):
        samples, instances = self.load()
        self.assertEqual([len(samples[key]) for key in ("train", "val", "test")], [1, 1, 1])
        self.assertEqual(len(instances), 3)
        target = samples["train"][0]["target"]
        self.assertEqual(list(json.loads(target)), ["appearance", "action", "location", "relation"])
        self.assertNotIn("LEAKY_REASON", target)

    def test_partial_teacher_attributes(self):
        self.results[0]["attributes"] = {"relation": [], "appearance": ["dark stripe"]}
        self._save_files()
        samples, _ = self.load()
        self.assertEqual(json.loads(samples["train"][0]["target"]),
                         {"appearance": ["dark stripe"], "relation": []})
        for invalid in ({}, {"appearance": [], "extra": []}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    serialize_attributes(invalid)

    def test_three_views_and_no_target_leakage(self):
        samples, instances = self.load()
        cropper = AdaptiveCropper.from_instances(instances, CropConfig(output_size=64))
        dataset = VLMAttributeDataset(samples["train"], self.images, cropper)
        item = dataset[0]
        self.assertEqual([part["type"] for part in item["messages"][0]["content"]],
                         ["image", "image", "image", "text"])
        self.assertEqual(len(item["images"]), 3)
        self.assertEqual(item["images"][1].size, (64, 64))
        self.assertEqual(item["images"][2].size, (64, 64))
        prompt = item["messages"][0]["content"][-1]["text"]
        for excluded in ("SECRET_CAPTION", "LEAKY_REASON", "bright orange"):
            self.assertNotIn(excluded, prompt)
        self.assertIn("Category: pen", prompt)
        self.assertIn("Known attributes/context: red", prompt)

    def test_reject_duplicate_teacher_ids(self):
        self.results.append(self.results[0])
        self._save_files()
        with self.assertRaisesRegex(ValueError, "重复 instance_id"):
            self.load()

    def test_reject_missing_instance(self):
        self.results[0]["instance_id"] = 999
        self._save_files()
        with self.assertRaisesRegex(ValueError, "找不到对应实例"):
            self.load()

    def test_reject_image_split_leakage(self):
        self.splits["val"][0] = ["train.jpg", "DIFFERENT_CAPTION"]
        self._save_files()
        with self.assertRaisesRegex(ValueError, "同一图像出现在多个划分"):
            self.load()

    def test_reject_bad_attributes_and_missing_image(self):
        self.results[0]["attributes"] = {**ATTRIBUTES, "appearance": [3]}
        self._save_files()
        with self.assertRaisesRegex(ValueError, "字符串列表"):
            self.load()
        self.results[0]["attributes"] = ATTRIBUTES
        (self.images / "train.jpg").unlink()
        self._save_files()
        with self.assertRaises(FileNotFoundError):
            self.load()
        (self.images / "train.jpg").write_bytes(b"not a real image")
        with self.assertRaisesRegex(ValueError, "原始图片无法读取"):
            self.load()

    def test_reject_empty_split(self):
        self.results.pop()
        self._save_files()
        with self.assertRaisesRegex(ValueError, "test 划分为空"):
            self.load()

    def test_existing_online_results(self):
        result = ROOT / "exp/exp_0909/result.json"
        images = Path("F:/REC-8K/rec-8k")
        if not result.is_file() or not images.is_dir():
            self.skipTest("本机没有在线标注结果或 REC-8K 图片")
        samples, _ = load_sft_samples([result], ROOT / "anno/instance_data.json",
                                      ROOT / "anno/splits.json", images)
        if sum(map(len, samples.values())) == 101:
            self.assertEqual([len(samples[key]) for key in ("train", "val", "test")],
                             [58, 17, 26])


class TestVLMCompletionCollator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MODEL_PATH.is_dir():
            raise unittest.SkipTest("本机没有 Qwen3.5-0.8B processor")
        from transformers import AutoProcessor
        cls.processor = AutoProcessor.from_pretrained(str(MODEL_PATH))

    def make_feature(self, instance_id, target):
        images = [Image.new("RGB", (64 + index * 16, 64), (20, 30, 40)) for index in range(3)]
        messages = [{"role": "user", "content": [
                                                    {"type": "image", "image": image} for image in images
                                                ] + [{"type": "text", "text": "Describe the target"}]}]
        return {"instance_id": instance_id, "messages": messages,
                "images": images, "target": target}

    def test_image_prompt_padding_and_answer_mask(self):
        features = [self.make_feature(0, serialize_attributes(ATTRIBUTES)),
                    self.make_feature(1, '{"appearance":[]}')]
        batch = VLMCompletionCollator(self.processor)(features)
        self.assertEqual(batch["input_ids"].shape, batch["labels"].shape)
        self.assertEqual(batch["input_ids"].shape, batch["attention_mask"].shape)
        self.assertEqual(batch["input_ids"].shape, batch["mm_token_type_ids"].shape)
        self.assertEqual(batch["image_grid_thw"].shape, (6, 3))
        self.assertEqual(batch["pixel_values"].shape[0],
                         int(batch["image_grid_thw"].prod(dim=1).sum().item()))
        image_token_id = self.processor.tokenizer.convert_tokens_to_ids("<|image_pad|>")
        for index, feature in enumerate(features):
            valid = batch["attention_mask"][index].bool()
            labels = batch["labels"][index]
            prompt = self.processor.apply_chat_template(
                feature["messages"], tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
            expected = self.processor(text=prompt, images=feature["images"], return_tensors="pt")
            prompt_length = expected["input_ids"].shape[1]
            self.assertTrue(torch.equal(batch["input_ids"][index, :prompt_length],
                                        expected["input_ids"][0]))
            self.assertTrue(torch.all(labels[:prompt_length] == -100))
            self.assertTrue(torch.all(labels[~valid] == -100))
            self.assertTrue(torch.all(labels[batch["input_ids"][index] == image_token_id] == -100))
            answer_ids = labels[labels != -100].tolist()
            self.assertEqual(self.processor.tokenizer.decode(answer_ids),
                             feature["target"] + "<|im_end|>")
            self.assertGreater((labels == -100).sum().item(), len(answer_ids))
        self.assertGreater((batch["attention_mask"] == 0).sum().item(), 0)

    def test_reject_truncated_answer(self):
        feature = self.make_feature(17, serialize_attributes(ATTRIBUTES))
        with self.assertRaisesRegex(ValueError, "实例 17 长度"):
            VLMCompletionCollator(self.processor, max_length=8)([feature])


class TestLoss(unittest.TestCase):
    def test_token_weighted_loss(self):
        self.assertEqual(mean_token_loss(5 * 2 + 1 * 8, 10), 1.8)
        labels = torch.tensor([[-100, -100, 3, 4, -100], [-100, -100, 6, -100, -100]])
        self.assertEqual(completion_token_count(labels), 3)
        batch = {"input_ids": torch.zeros_like(labels), "labels": labels,
                 "image_grid_thw": torch.ones((6, 3), dtype=torch.long)}
        sliced = completion_loss_inputs(batch)
        self.assertEqual(sliced["logits_to_keep"], 4)
        self.assertTrue(torch.equal(sliced["labels"], labels[:, 1:]))
        self.assertEqual(completion_token_count(sliced["labels"]), 3)
        self.assertIs(sliced["image_grid_thw"], batch["image_grid_thw"])
        with self.assertRaises(ValueError):
            mean_token_loss(0, 0)

    def test_save_full_model_restores_generation_cache(self):
        class Model:
            def __init__(self):
                self.config = SimpleNamespace(text_config=SimpleNamespace(use_cache=False))
                self.saved_with_cache = None

            def save_pretrained(self, path, safe_serialization):
                self.saved_with_cache = self.config.text_config.use_cache

        model = Model()
        processor = SimpleNamespace(save_pretrained=lambda path: None)
        accelerator = SimpleNamespace(is_main_process=True,
                                      unwrap_model=lambda value: value,
                                      wait_for_everyone=lambda: None)
        with tempfile.TemporaryDirectory() as directory:
            save_best(model, processor, directory, accelerator, {"finetune_mode": "full"})
            self.assertTrue(model.saved_with_cache)
            self.assertFalse(model.config.text_config.use_cache)
            self.assertTrue((Path(directory) / "sft_config.json").is_file())

    def test_lora_modules_only_from_language_model(self):
        class Dummy(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.model = torch.nn.Module()
                self.model.language_model = torch.nn.Module()
                self.model.language_model.layers = torch.nn.ModuleList([
                    torch.nn.Module() for _ in range(2)
                ])
                first = self.model.language_model.layers[0]
                first.self_attn = torch.nn.Module()
                first.self_attn.q_proj = torch.nn.Linear(2, 2)
                second = self.model.language_model.layers[1]
                second.linear_attn = torch.nn.Module()
                second.linear_attn.in_proj_qkv = torch.nn.Linear(2, 2)
                self.model.visual = torch.nn.Module()
                self.model.visual.q_proj = torch.nn.Linear(2, 2)

        modules = select_lora_modules(Dummy())
        self.assertEqual(len(modules), 2)
        self.assertFalse(any("visual" in name for name in modules))


if __name__ == "__main__":
    unittest.main()
