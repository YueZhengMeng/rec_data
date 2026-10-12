from __future__ import annotations

import math
import os
import re
from typing import Any

import torch
from PIL import Image
from accelerate import Accelerator
from crop import AdaptiveCropper
from peft import PeftModel
from prompts import build_extract_attributes_prompt
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig
from transformers.generation.logits_process import LogitsProcessor, LogitsProcessorList


class PresencePenaltyLogitsProcessor(LogitsProcessor):
    """对已经出现过的 token 施加一次固定的 presence penalty。"""

    def __init__(self, penalty: float):
        self.penalty = penalty

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        if self.penalty == 0:
            return scores

        appeared = torch.zeros_like(scores, dtype=torch.bool)
        appeared.scatter_(1, input_ids, True)
        return scores - appeared.to(scores.dtype) * self.penalty


class ConfidenceRecorder(LogitsProcessor):
    """逐生成步计算置信度，只暂存上一步的单个词表 logits。"""

    def __init__(
            self,
            eos_token_ids: set[int],
            pad_token_id: int | None,
            record_token_confidences: bool = False,
    ):
        self.eos_token_ids = eos_token_ids
        self.pad_token_id = pad_token_id
        self.record_token_confidences = record_token_confidences
        self.previous_scores = None
        self.active = None
        self.confidence_sums = None
        self.log_probability_sums = None
        self.token_counts = None
        self.token_confidences = None

    def _initialize(self, scores: torch.Tensor) -> None:
        batch_size = scores.shape[0]
        self.active = torch.ones(batch_size, dtype=torch.bool, device=scores.device)
        self.confidence_sums = torch.zeros(batch_size, dtype=torch.float64, device=scores.device)
        self.log_probability_sums = torch.zeros(batch_size, dtype=torch.float64, device=scores.device)
        self.token_counts = torch.zeros(batch_size, dtype=torch.long, device=scores.device)
        self.token_confidences = [[] for _ in range(batch_size)]

    def _consume_previous_scores(self, token_ids: torch.Tensor) -> None:
        """取得上一步实际生成 token 的概率后，立即释放完整词表 logits。"""
        if self.previous_scores is None:
            return

        token_ids = token_ids.to(self.previous_scores.device)
        # PAD 不属于有效输出；EOS 属于有效生成 token，需要计入置信度后再停止。
        valid = self.active.clone()
        pad_is_eos = self.pad_token_id in self.eos_token_ids
        if self.pad_token_id is not None and not pad_is_eos:
            valid &= token_ids != self.pad_token_id

        selected_logits = self.previous_scores.gather(1, token_ids.unsqueeze(1)).squeeze(1)
        log_normalizers = torch.logsumexp(self.previous_scores, dim=-1)
        log_probabilities = selected_logits - log_normalizers
        probabilities = torch.exp(log_probabilities).clamp(0.0, 1.0)

        self.confidence_sums[valid] += probabilities[valid].double()
        self.log_probability_sums[valid] += log_probabilities[valid].double()
        self.token_counts[valid] += 1

        if self.record_token_confidences:
            probability_values = probabilities.detach().cpu().tolist()
            valid_values = valid.detach().cpu().tolist()
            for index, (probability, is_valid) in enumerate(zip(probability_values, valid_values)):
                if is_valid:
                    self.token_confidences[index].append(float(probability))

        if self.eos_token_ids:
            eos_mask = torch.zeros_like(self.active)
            for eos_token_id in self.eos_token_ids:
                eos_mask |= token_ids == eos_token_id
            self.active &= ~eos_mask
        if self.pad_token_id is not None and not pad_is_eos:
            self.active &= token_ids != self.pad_token_id

        # 当前 token 的标量统计已经保存，不再保留 [batch, vocab] 张量。
        del selected_logits, log_normalizers, log_probabilities, probabilities
        del self.previous_scores
        self.previous_scores = None

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        if self.active is None:
            self._initialize(scores)
        else:
            # 当前调用发生时，input_ids 最后一个位置就是上一步实际采样的 token。
            self._consume_previous_scores(input_ids[:, -1])

        # 不复制 logits，只额外保留一个生成步；下一步到来时立即压缩为标量。
        self.previous_scores = scores.detach()
        return scores

    def finalize(self, generated_ids: torch.Tensor) -> list[dict[str, Any]]:
        """生成结束后处理最后一个 token，并返回每条序列的聚合指标。"""
        if self.previous_scores is not None and generated_ids.shape[1] > 0:
            self._consume_previous_scores(generated_ids[:, -1])

        if self.token_counts is None:
            return []

        confidence_sums = self.confidence_sums.detach().cpu().tolist()
        log_probability_sums = self.log_probability_sums.detach().cpu().tolist()
        token_counts = self.token_counts.detach().cpu().tolist()
        metrics = []
        for index, token_count in enumerate(token_counts):
            if token_count > 0:
                average_confidence = confidence_sums[index] / token_count
                ppl = math.exp(-log_probability_sums[index] / token_count)
            else:
                average_confidence = None
                ppl = None
            metrics.append({
                "token_confidences": self.token_confidences[index],
                "average_confidence": average_confidence,
                "ppl": ppl,
                "output_tokens": token_count,
            })
        return metrics


class LocalAttributesExtractor:
    """使用本地 Qwen3.5 模型提取单个实例的可见属性。"""

    def __init__(
            self,
            model_path: str,
            image_dir: str,
            cropper: AdaptiveCropper,
            accelerator: Accelerator,
            model_mode: str = "base",
            base_model_path: str | None = None,
            adapter_path: str | None = None,
            enable_thinking: bool = False,
            max_new_tokens: int = 1024,
            do_sample: bool = True,
            temperature: float = 0.7,
            top_p: float = 0.8,
            top_k: int = 20,
            min_p: float = 0.0,
            presence_penalty: float = 1.5,
            repetition_penalty: float = 1.0,
            record_token_confidences: bool = False,
    ):
        self.model_path = model_path
        self.image_dir = image_dir
        self.cropper = cropper
        self.model_mode = model_mode.lower()
        self.base_model_path = base_model_path or model_path
        self.adapter_path = adapter_path
        self.enable_thinking = enable_thinking
        self.max_new_tokens = max_new_tokens
        self.do_sample = do_sample
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.min_p = min_p
        self.presence_penalty = presence_penalty
        self.repetition_penalty = repetition_penalty
        self.record_token_confidences = record_token_confidences

        if self.model_mode not in {"base", "nf4", "full", "lora", "qlora"}:
            raise ValueError("model_mode 必须是 base、nf4、full、lora 或 qlora")
        if self.model_mode in {"lora", "qlora"} and not self.adapter_path:
            raise ValueError(f"{self.model_mode} 模式必须提供 adapter_path")

        # 创建 HuggingFace Accelerator；保持与 inference.py 一致，
        # 由 Accelerator 统一管理 bf16 混合精度推理和运行设备。
        self.accelerator = accelerator
        self.device = self.accelerator.device
        self.torch_dtype = torch.bfloat16 if self.device.type == "cuda" else torch.float32

        self.accelerator.print(
            f"⏳ 加载本地模型: {self.model_path}，"
            f"模式: {self.model_mode}，设备数: {self.accelerator.num_processes}"
        )
        self.processor = AutoProcessor.from_pretrained(self.base_model_path)
        self.processor.tokenizer.padding_side = "left"
        if self.processor.tokenizer.pad_token is None:
            self.processor.tokenizer.pad_token = self.processor.tokenizer.eos_token

        self.model = self._load_model()
        self.model.eval()
        self.accelerator.print("✅ 本地模型加载完成")

    def _get_device_map(self):
        """让每个 Accelerate 进程只在对应的单张 GPU 上加载模型。"""
        if self.device.type == "cuda":
            return {"": self.accelerator.local_process_index}
        return {"": self.device}

    def _prepare_model(self, model):
        """关闭推理模型梯度，避免多卡推理时创建不必要的 DDP 梯度同步。"""
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        return self.accelerator.prepare(model)

    def _load_base_model(self, quantized: bool = False):
        """加载基础模型；量化模型由 Transformers/Accelerate 管理设备映射。"""
        if quantized:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            self.accelerator.print("✅ 启用 nf4 量化")
        else:
            quantization_config = None

        model = AutoModelForImageTextToText.from_pretrained(
            self.base_model_path,
            dtype=self.torch_dtype,
            # 每个进程加载一份完整模型到自己的 GPU，执行数据并行推理。
            device_map=self._get_device_map(),
            quantization_config=quantization_config
        )
        return model

    def _load_model(self):
        # --- 官方权重 ---
        if self.model_mode in ["base", "nf4"]:
            # --- 是否量化为 NF4 ---
            quantized = self.model_mode == "nf4"
            model = self._load_base_model(quantized=quantized)
            return self._prepare_model(model)

        # --- 全参数微调权重模式 ---
        if self.model_mode == "full":
            model = AutoModelForImageTextToText.from_pretrained(
                self.model_path,
                dtype=self.torch_dtype,
                device_map=self._get_device_map()
            )
            return self._prepare_model(model)

        # --- LoRA 或 QLoRA 模式 ---
        quantized = self.model_mode == "qlora"
        base_model = self._load_base_model(quantized=quantized)
        model = PeftModel.from_pretrained(base_model, self.adapter_path)
        return self._prepare_model(model)

    def _build_message(self, instance_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Image.Image]]:
        """读取图片、建立三种视图，并构造 Transformers 多模态消息。"""
        image_path = os.path.join(self.image_dir, instance_data["image_name"])
        with Image.open(image_path) as source_image:
            image_global = source_image.convert("RGB")
            crops = self.cropper.create_crops(
                image_global,
                image_name=instance_data["image_name"],
                category=instance_data["category"],
                point=instance_data["point"],
            )

        text_prompt = build_extract_attributes_prompt(
            instance_data["category"],
            instance_data.get("attributes", ""),
        )
        # Qwen3.5 的 processor 识别 type=image 且 image 为 PIL.Image 的输入。
        content = [
            {"type": "image", "image": crops.full_image},
            {"type": "image", "image": crops.context_image},
            {"type": "image", "image": crops.fine_image},
            {"type": "text", "text": text_prompt},
        ]
        messages = [{"role": "user", "content": content}]
        images = [crops.full_image, crops.context_image, crops.fine_image]
        return messages, images

    @staticmethod
    def _split_thinking(text: str) -> tuple[str, str]:
        """分离 Qwen 格式的思考段和最终回答。"""
        if not isinstance(text, str):
            return "", ""

        if "</think>" in text:
            reasoning, content = text.split("</think>", 1)
            reasoning = reasoning.replace("<think>", "", 1).strip()
            return content.strip(), reasoning

        # 非思考模式通常不会返回标签，但清理残留标签可以提高可读性。
        content = re.sub(r"</?think>", "", text, flags=re.IGNORECASE).strip()
        return content, ""

    @staticmethod
    def _get_eos_ids(tokenizer) -> set[int]:
        """统一处理 tokenizer 中 eos_token_id 为整数或列表的情况。"""
        eos_token_id = tokenizer.eos_token_id
        if eos_token_id is None:
            return set()
        if isinstance(eos_token_id, int):
            return {eos_token_id}
        return {int(token_id) for token_id in eos_token_id}

    def _get_effective_token_ids(self, generated_ids: torch.Tensor) -> list[list[int]]:
        """移除批量生成的 PAD，并保留首个 EOS 之前的实际输出 token。"""
        pad_token_id = self.processor.tokenizer.pad_token_id
        eos_ids = self._get_eos_ids(self.processor.tokenizer)
        effective_token_ids = []

        for sequence in generated_ids.tolist():
            sequence_ids = []
            for token_id in sequence:
                if token_id in eos_ids:
                    sequence_ids.append(token_id)
                    break
                if token_id == pad_token_id:
                    break
                sequence_ids.append(token_id)
            effective_token_ids.append(sequence_ids)
        return effective_token_ids

    def infer_batch(self, instance_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量生成回答，返回原始文本、token 置信度和 PPL。"""
        if not instance_list:
            return []

        all_texts = []
        all_images = []
        for instance_data in instance_list:
            messages, images = self._build_message(instance_data)
            input_text = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=self.enable_thinking,
            )
            all_texts.append(input_text)
            # processor 的 image 列表按照文本中出现的顺序展开。
            all_images.extend(images)

        inputs = self.processor(
            text=all_texts,
            images=all_images,
            padding=True,
            return_tensors="pt",
        )
        inputs = {
            key: value.to(self.device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }

        input_length = inputs["input_ids"].shape[1]
        confidence_recorder = ConfidenceRecorder(
            eos_token_ids=self._get_eos_ids(self.processor.tokenizer),
            pad_token_id=self.processor.tokenizer.pad_token_id,
            record_token_confidences=self.record_token_confidences,
        )
        logits_processors = LogitsProcessorList()
        if self.presence_penalty != 0:
            logits_processors.append(PresencePenaltyLogitsProcessor(self.presence_penalty))
        # 自定义 processor 在 temperature/top-k/top-p 之前运行，记录模型原始分布。
        logits_processors.append(confidence_recorder)

        generate_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "pad_token_id": self.processor.tokenizer.pad_token_id,
            "eos_token_id": self.processor.tokenizer.eos_token_id,
            "do_sample": self.do_sample,
            "repetition_penalty": self.repetition_penalty,
            "logits_processor": logits_processors,
        }
        if self.do_sample:
            generate_kwargs.update({
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
            })
            # min_p=0 表示禁用；不传入可以避免每一步额外计算一次完整 softmax。
            if self.min_p > 0:
                generate_kwargs["min_p"] = self.min_p

        # 使用 Accelerator 的 autocast 执行 bf16 混合精度推理。
        # 不启用 output_logits/output_scores，避免保留所有生成步的完整词表张量。
        with torch.inference_mode(), self.accelerator.autocast():
            unwrapped_model = self.accelerator.unwrap_model(self.model)
            generation_output = unwrapped_model.generate(**inputs, **generate_kwargs)
            generated_ids = generation_output[:, input_length:]
            # Recorder 内部张量在 inference_mode 中创建，最后一步也必须在同一模式下完成。
            token_metrics = confidence_recorder.finalize(generated_ids)

        effective_token_ids = self._get_effective_token_ids(generated_ids)
        if len(token_metrics) != len(instance_list):
            raise RuntimeError("生成结果数量与置信度统计数量不一致")
        for index, sequence_ids in enumerate(effective_token_ids):
            if token_metrics[index]["output_tokens"] != len(sequence_ids):
                raise RuntimeError("生成 token 数量与置信度统计数量不一致")

        output_texts = self.processor.tokenizer.batch_decode(
            generated_ids,
            skip_special_tokens=True,
        )
        input_tokens = inputs["attention_mask"].sum(dim=1).tolist()

        results = []
        for index, (instance_data, raw_text) in enumerate(zip(instance_list, output_texts)):
            content, reasoning = self._split_thinking(raw_text)
            metrics = token_metrics[index]
            results.append({
                "instance_id": instance_data["instance_id"],
                "content": content,
                "reasoning_content": reasoning,
                "input_tokens": int(input_tokens[index]),
                "output_tokens": metrics["output_tokens"],
                "output_token_ids": (
                    effective_token_ids[index] if self.record_token_confidences else []
                ),
                "token_confidences": metrics["token_confidences"],
                "average_confidence": metrics["average_confidence"],
                "ppl": metrics["ppl"],
                "generation_failed": not bool(content.strip()),
            })
        return results

    def extract_attributes(self, instance_data: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        """保留与在线 AttributesExtractor 类似的单条调用接口。"""
        result = self.infer_batch([instance_data])[0]
        return result["content"], result
