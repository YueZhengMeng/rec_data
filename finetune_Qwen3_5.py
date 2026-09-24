"""使用在线 MLLM 标注的三视图数据对 Qwen3.5 进行多模态 SFT。"""

import argparse
import gc
import json
import os
import random
import re
import sys
import time
from itertools import islice
from pathlib import Path

os.environ["TOKENIZERS_PARALLELISM"] = "false"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import torch
from accelerate import Accelerator
from peft import LoraConfig, PeftModel, TaskType, get_peft_model, prepare_model_for_kbit_training
from torch.optim import AdamW
from torch.optim.lr_scheduler import OneCycleLR
from torch.utils.data import DataLoader, Subset
from tqdm import tqdm
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

from crop import AdaptiveCropper, CropConfig
from sft_qwen3_5_data import VLMAttributeDataset, VLMCompletionCollator, load_sft_samples

ROOT = Path(__file__).resolve().parent


class Logger:
    """沿用旧训练脚本的主进程打印和实时日志文件。"""

    def __init__(self, work_dir, accelerator):
        self.is_main = accelerator.is_main_process
        self.file = None
        if self.is_main:
            self.file = open(Path(work_dir) / "logs.txt", "w", encoding="utf-8")

    def log(self, message):
        if self.is_main:
            print(message, flush=True)
            self.file.write(str(message) + "\n")
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()


def seed_everything(seed):
    # 与旧项目保持一致，固定 Python、PyTorch 与各张 GPU 的随机种子。
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def parse_args():
    args = argparse.ArgumentParser(description="Qwen3.5 三视图实例属性提取 SFT")
    args.add_argument("--seed", type=int, default=42)
    args.add_argument("--epochs", type=int, default=50)
    args.add_argument("--batch-size", type=int, default=1)
    args.add_argument("--peak-learning-rate", type=float, default=None)
    args.add_argument("--max-length", type=int, default=16384)
    args.add_argument("--early-stop-patience", type=int, default=5)
    args.add_argument("--model-name-or-path", default=None,
                      help="默认使用 config.LOCAL_CONFIG 中的 base_model_path")
    args.add_argument("--finetune-mode", choices=["full", "lora", "qlora"], default="lora")
    args.add_argument("--lora-dropout", type=float, default=0.05)
    args.add_argument("--result-files", nargs="+", default=[str(ROOT / "exp/exp_0909/result.json")])
    args.add_argument("--instances-file", default=str(ROOT / "anno/instance_data.json"))
    args.add_argument("--splits-file", default=str(ROOT / "anno/splits.json"))
    args.add_argument("--image-dir", default=None, help="默认使用 config.CONFIG['image_dir']")
    args.add_argument("--output-dir", default=str(ROOT / "exp/sft_qwen3_5"))
    args.add_argument("--max-train-samples", type=int, default=None,
                      help="仅用于小规模烟测，每个划分均从头取指定条数")
    args.add_argument("--max-eval-samples", type=int, default=None)
    args.add_argument("--max-train-batches", type=int, default=None)
    args.add_argument("--preflight", action="store_true", help="只检查数据与划分，不加载模型")
    return args.parse_args()


def select_lora_modules(model):
    """只给 Qwen3.5 语言侧全注意力、线性注意力和 MLP 投影添加 Adapter。"""
    pattern = re.compile(
        r"^model\.language_model\.layers\.\d+\."
        r"(?:self_attn\.(?:q_proj|k_proj|v_proj|o_proj)|"
        r"linear_attn\.(?:in_proj_qkv|in_proj_z|in_proj_a|in_proj_b|out_proj)|"
        r"mlp\.(?:gate_proj|up_proj|down_proj))$"
    )
    modules = [name for name, module in model.named_modules()
               if pattern.fullmatch(name) and isinstance(module, torch.nn.Linear)]
    if not modules or not any(".self_attn." in name for name in modules) or not any(
            ".linear_attn." in name for name in modules):
        raise ValueError("未找到 Qwen3.5 语言侧完整的注意力投影，检查模型架构与 LoRA 配置")
    return modules


def load_model(model_path, mode, accelerator, dtype, lora_dropout=0.05):
    # --- QLoRA 使用 NF4 量化；其余模式正常加载模型。 ---
    quantization_config = None
    if mode == "qlora":
        if accelerator.device.type != "cuda":
            raise ValueError("QLoRA 需要 CUDA 和 bitsandbytes")
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    load_kwargs = {"dtype": dtype, "quantization_config": quantization_config}
    if mode == "qlora":
        # 量化模型必须在每个 Accelerate 进程对应的 GPU 上直接加载。
        load_kwargs["device_map"] = {"": accelerator.local_process_index}
    model = AutoModelForImageTextToText.from_pretrained(model_path, **load_kwargs)
    model.config.text_config.use_cache = False

    if mode == "qlora":
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
        )
    else:
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )

    # --- LoRA 与 QLoRA 只训练语言侧投影，图像编码器仍通过预训练权重提供视觉特征。 ---
    if mode in {"lora", "qlora"}:
        target_modules = select_lora_modules(model)
        lora_config = LoraConfig(
            r=32, lora_alpha=64, target_modules=target_modules,
            lora_dropout=lora_dropout, bias="none", task_type=TaskType.CAUSAL_LM,
        )
        if mode == "lora":
            model.enable_input_require_grads()
        model = get_peft_model(model, lora_config)
        accelerator.print(f"LoRA 命中 {len(target_modules)} 个语言投影模块")
        if accelerator.is_main_process:
            model.print_trainable_parameters()

    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    if not trainable:
        raise ValueError("模型中没有可训练参数")
    return model


def completion_token_count(labels):
    # Transformers 的 causal loss 在内部执行 shift，因此首个 label 不参与预测。
    return (labels[:, 1:] != -100).sum().item()


def completion_loss_inputs(batch):
    """只计算答案附近的 logits；长图像前缀不需要生成整个词表的 logits。"""
    positions = (batch["labels"] != -100).nonzero(as_tuple=False)
    if positions.numel() == 0:
        raise ValueError("当前 batch 没有有效的 assistant 监督 token")
    start = max(positions[:, 1].min().item() - 1, 0)
    # Qwen3.5 的 logits_to_keep 选取末尾 hidden states；标签必须同步截取。
    # 保留答案前一个 token，模型内部 shift 后才能预测答案的第一个 token。
    return {**batch, "labels": batch["labels"][:, start:],
            "logits_to_keep": batch["input_ids"].shape[1] - start}


def mean_token_loss(loss_sum, token_count):
    if token_count <= 0:
        raise ValueError("当前数据集没有有效的 assistant 监督 token")
    return loss_sum / token_count


def reduce_token_loss(loss_sum, token_count, accelerator):
    totals = torch.tensor([loss_sum, token_count], dtype=torch.float64, device=accelerator.device)
    totals = accelerator.reduce(totals, reduction="sum")
    return mean_token_loss(*totals.tolist())


def train_one_epoch(model, dataloader, optimizer, lr_scheduler, accelerator, max_batches=None):
    model.train()
    loss_sum = 0.0
    token_count = 0
    batches = dataloader if max_batches is None else islice(dataloader, max_batches)
    total = len(dataloader) if max_batches is None else min(len(dataloader), max_batches)
    for batch in tqdm(batches, total=total, disable=not accelerator.is_main_process):
        optimizer.zero_grad()
        outputs = model(**completion_loss_inputs(batch))
        loss = outputs.loss
        if not torch.isfinite(loss):
            raise ValueError("训练 loss 出现 NaN/Inf，请检查标签及学习率")
        accelerator.backward(loss)
        optimizer.step()
        if not accelerator.optimizer_step_was_skipped:
            lr_scheduler.step()
        count = completion_token_count(batch["labels"])
        loss_sum += loss.detach().item() * count
        token_count += count
    return reduce_token_loss(loss_sum, token_count, accelerator)


@torch.no_grad()
def evaluate_loss(model, dataset, collator, batch_size, accelerator):
    """按进程精确切分，不使用会重复填充样本的分布式验证 DataLoader。"""
    model.eval()
    indices = range(accelerator.process_index, len(dataset), accelerator.num_processes)
    dataloader = DataLoader(Subset(dataset, list(indices)), batch_size=batch_size,
                            shuffle=False, collate_fn=collator, num_workers=0,
                            pin_memory=accelerator.device.type == "cuda")
    loss_sum = 0.0
    token_count = 0
    for batch in tqdm(dataloader, disable=not accelerator.is_main_process):
        batch = {key: value.to(accelerator.device) for key, value in batch.items()}
        with accelerator.autocast():
            loss = model(**completion_loss_inputs(batch)).loss
        if not torch.isfinite(loss):
            raise ValueError("评估 loss 出现 NaN/Inf，请检查模型或标签")
        count = completion_token_count(batch["labels"])
        loss_sum += loss.item() * count
        token_count += count
    return reduce_token_loss(loss_sum, token_count, accelerator)


def save_best(model, processor, model_dir, accelerator, metadata):
    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        unwrapped = accelerator.unwrap_model(model)
        # 全参权重以后用于生成；保存时恢复 KV cache，训练仍保持关闭。
        if metadata["finetune_mode"] == "full":
            unwrapped.config.text_config.use_cache = True
        try:
            unwrapped.save_pretrained(model_dir, safe_serialization=True)
        finally:
            if metadata["finetune_mode"] == "full":
                unwrapped.config.text_config.use_cache = False
        processor.save_pretrained(model_dir)
        with open(Path(model_dir) / "sft_config.json", "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
    accelerator.wait_for_everyone()


def reload_best(model_path, model_dir, mode, accelerator, dtype):
    device_map = {"": accelerator.local_process_index} if accelerator.device.type == "cuda" else None
    if mode == "full":
        model = AutoModelForImageTextToText.from_pretrained(model_dir, dtype=dtype, device_map=device_map)
    else:
        config = None
        if mode == "qlora":
            config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                        bnb_4bit_compute_dtype=torch.bfloat16,
                                        bnb_4bit_use_double_quant=True)
        base_model = AutoModelForImageTextToText.from_pretrained(
            model_path, dtype=dtype, quantization_config=config, device_map=device_map
        )
        model = PeftModel.from_pretrained(base_model, model_dir)
    if device_map is None:
        model.to(accelerator.device)
    return model


def main():
    args = parse_args()
    if args.epochs < 1 or args.batch_size < 1 or args.max_length < 1 or args.early_stop_patience < 1:
        raise ValueError("epochs、batch-size、max-length 和 early-stop-patience 必须大于 0")
    for value in (args.max_train_samples, args.max_eval_samples, args.max_train_batches):
        if value is not None and value < 1:
            raise ValueError("烟测所用的样本数和训练 batch 数必须大于 0")
    if args.peak_learning_rate is not None and args.peak_learning_rate <= 0:
        raise ValueError("peak-learning-rate 必须大于 0")

    # 此处才加载项目配置，避免单独测试数据模块时创建在线实验结果文件。
    from config import CONFIG, LOCAL_CONFIG
    model_path = args.model_name_or_path or LOCAL_CONFIG["base_model_path"]
    image_dir = args.image_dir or CONFIG["image_dir"]
    learning_rate = (args.peak_learning_rate if args.peak_learning_rate is not None
                     else 1e-5 if args.finetune_mode == "full" else 1e-4)
    accelerator = Accelerator(mixed_precision="bf16" if torch.cuda.is_available() and
                                                        torch.cuda.is_bf16_supported() else "no")
    dtype = torch.bfloat16 if accelerator.mixed_precision == "bf16" else torch.float32
    seed_everything(args.seed + accelerator.process_index)

    # 先用全部点构建密度索引，再筛选在线标注，保证裁剪尺度与推理时完全一致。
    samples, instances = load_sft_samples(args.result_files, args.instances_file,
                                          args.splits_file, image_dir)
    counts = {split: len(rows) for split, rows in samples.items()}
    accelerator.print(f"在线标注 train/val/test: {counts}")
    if args.preflight:
        return
    cropper = AdaptiveCropper.from_instances(instances, CropConfig(**CONFIG["crop"]))
    if args.max_train_samples is not None:
        samples["train"] = samples["train"][:args.max_train_samples]
    if args.max_eval_samples is not None:
        for split in ("val", "test"):
            samples[split] = samples[split][:args.max_eval_samples]
    datasets = {split: VLMAttributeDataset(rows, image_dir, cropper)
                for split, rows in samples.items()}
    counts = {split: len(dataset) for split, dataset in datasets.items()}

    output_dir = Path(args.output_dir)
    # 只由主进程检查旧实验，随后同步结果；避免多卡上其它进程看到新建目录误报覆盖。
    directory_in_use = int(output_dir.exists() and any(output_dir.iterdir())) if accelerator.is_main_process else 0
    directory_in_use = accelerator.reduce(
        torch.tensor(directory_in_use, device=accelerator.device), reduction="sum"
    ).item()
    if directory_in_use:
        raise FileExistsError(f"输出目录非空，请换一个 --output-dir: {output_dir}")
    if accelerator.is_main_process:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "best_model").mkdir()
    accelerator.wait_for_everyone()
    logger = Logger(output_dir, accelerator)
    try:
        logger.log(f"随机种子: {args.seed}\t设备: {accelerator.device}\t数据量: {counts}")
        logger.log(f"模型: {model_path}\t微调模式: {args.finetune_mode}\t学习率: {learning_rate}")
        processor = AutoProcessor.from_pretrained(model_path)
        collator = VLMCompletionCollator(processor, args.max_length)
        dataloader_train = DataLoader(
            datasets["train"], batch_size=args.batch_size, shuffle=True, collate_fn=collator,
            pin_memory=accelerator.device.type == "cuda", num_workers=0,
            generator=torch.Generator().manual_seed(args.seed),
        )
        logger.log("开始加载预训练模型")
        model = load_model(model_path, args.finetune_mode, accelerator, dtype, args.lora_dropout)
        optimizer = AdamW((p for p in model.parameters() if p.requires_grad),
                          lr=learning_rate, betas=(0.9, 0.98), eps=1e-6, weight_decay=1e-5)
        model, optimizer, dataloader_train = accelerator.prepare(model, optimizer, dataloader_train)
        steps_every_epoch = min(len(dataloader_train), args.max_train_batches or len(dataloader_train))
        if steps_every_epoch < 1:
            raise ValueError("训练 DataLoader 没有 batch")
        total_train_steps = steps_every_epoch * args.epochs
        lr_scheduler = OneCycleLR(optimizer, max_lr=learning_rate,
                                  total_steps=total_train_steps, anneal_strategy="linear",
                                  pct_start=0.1, div_factor=25.0, final_div_factor=10000.0)
        logger.log(f"模型加载完成，总训练更新次数: {total_train_steps}")

        train_loss_list = []
        val_loss_list = []
        best_val_loss = float("inf")
        early_stop_count = 0
        model_dir = output_dir / "best_model"
        for epoch in range(args.epochs):
            logger.log(f"开始训练——Epoch {epoch + 1}/{args.epochs}")
            start_time = time.time()
            train_loss = train_one_epoch(model, dataloader_train, optimizer, lr_scheduler,
                                         accelerator, args.max_train_batches)
            # 不经过 DDP 包装器验证，允许各进程评测数量不同（包括某进程 0 条）。
            val_loss = evaluate_loss(accelerator.unwrap_model(model), datasets["val"],
                                     collator, args.batch_size, accelerator)
            train_loss_list.append(train_loss)
            val_loss_list.append(val_loss)
            logger.log(f"Epoch {epoch + 1}/{args.epochs} - Train Loss: {train_loss:.6f}"
                       f"\tValidation Loss: {val_loss:.6f}\tTime: {time.time() - start_time:.1f}s")

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                early_stop_count = 0
                metadata = {"base_model_path": model_path, "finetune_mode": args.finetune_mode,
                            "result_files": args.result_files, "sample_counts": counts,
                            "best_epoch": epoch + 1, "best_val_loss": best_val_loss,
                            "enable_thinking": False, "arguments": vars(args)}
                save_best(model, processor, model_dir, accelerator, metadata)
                logger.log("验证 loss 降低，已保存最佳模型")
            else:
                early_stop_count += 1
                if early_stop_count >= args.early_stop_patience:
                    logger.log("Early Stopping")
                    break

        if accelerator.is_main_process:
            with open(output_dir / "history.json", "w", encoding="utf-8") as file:
                json.dump({"train_loss": train_loss_list, "val_loss": val_loss_list}, file, indent=4)
            try:
                from matplotlib import pyplot as plt
                plt.plot(train_loss_list, label="train loss")
                plt.plot(val_loss_list, label="validation loss")
                plt.xlabel("epoch")
                plt.ylabel("loss")
                plt.legend()
                plt.savefig(output_dir / "loss.png")
                plt.close()
            except ImportError:
                logger.log("未安装 matplotlib，跳过 loss 曲线")
        accelerator.wait_for_everyone()

        # 测试集只在训练结束后，用重新加载的最佳权重评估一次。
        del model, optimizer, lr_scheduler, dataloader_train
        accelerator.free_memory()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.log("重新加载最佳权重，开始测试")
        best_model = reload_best(model_path, model_dir, args.finetune_mode, accelerator, dtype)
        test_loss = evaluate_loss(best_model, datasets["test"], collator, args.batch_size, accelerator)
        logger.log(f"Best Validation Loss: {best_val_loss:.6f}\tTest Loss: {test_loss:.6f}")
    finally:
        logger.close()


if __name__ == "__main__":
    main()
