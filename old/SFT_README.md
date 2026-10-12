# Qwen3.5 三视图属性提取 SFT

训练入口为 `finetune_Qwen3_5.py`，只使用 **在线推理**生成的 `attributes`，不会将教师模型的 `reasoning_content` 作为标签。输入保持与
`old/agents_local.py` 相同：标点原图、标点上下文裁剪、无标点局部裁剪以及共享提示词；输出为包含 `appearance`、`action`、
`location`、
`relation` 的 JSON。评价和早停依据只有 loss。

## 数据

默认读取 `exp/exp_0909/result.json`，按 `instance_id` 与 `../anno/instance_data.json` 关联，从
`config.CONFIG['image_dir']`
加载图片，并按 `anno/splits.json` 的图像/描述组合分为 train、val、test。默认结果文件目前有 101 条：58 train、17 val、26
test。后续标注可通过 `--result-files` 传入一个或多个在线结果文件；跨文件 `instance_id` 不得重复，三份划分都必须有样本。不要将
`exp/exp_local_qwen35/result.json` 当作在线教师标签——其中成功记录通常不保存 `attributes`。

```bash
python finetune_Qwen3_5.py --preflight
python -m unittest test_sft_qwen3_5.py
```

## 训练

依赖 PyTorch、Transformers（支持 Qwen3.5 的版本）、Accelerate、PEFT、Pillow、tqdm；QLoRA 另需 bitsandbytes。当前脚本不依赖 TRL。
`--model-name-or-path` 默认为 `config.LOCAL_CONFIG['base_model_path']`，`--image-dir` 默认为
`config.CONFIG['image_dir']`。

```bash
# 一次小规模端到端测试（请换成一个空的输出目录）
python finetune_Qwen3_5.py --epochs 1 --max-train-samples 2 --max-eval-samples 1 --max-train-batches 1 --output-dir exp/sft_smoke_01

# 正式 LoRA 训练；显存不足时可尝试 --finetune-mode qlora
python finetune_Qwen3_5.py --finetune-mode lora --epochs 50 --batch-size 1 --output-dir exp/sft_qwen3_5_lora

# 多张 CUDA 显卡上的数据并行训练
accelerate launch --multi_gpu --num_processes 2 finetune_Qwen3_5.py --finetune-mode lora --output-dir exp/sft_qwen3_5_2gpu
```

训练保存 `best_model/`（LoRA/QLoRA 为 Adapter，全参为完整模型）、`logs.txt`、`history.json`、`loss.png`
。新实验必须使用空目录，防止覆盖已有的最佳权重。可以通过 `--result-files`、`--instances-file`、`--splits-file`、`--image-dir`
指定其他数据；`--max-length` 默认为 16384，覆盖当前在线结果最长约 12980 token 的样本，超过限制会明确报错而不会截断图像或回答。训练仅为回答计算词表
logits，降低大图像提示词的显存消耗；但保持原始图片处理设置，以免与本地推理的视觉输入不一致。极长图像在 16 GB
显卡上仍可能内存不足，需要更大的内存/显存；小规模测试不等于完整训练。

## 加载训练后的模型

目前 `main_local.py` 已支持直接从命令行切换模型模式。LoRA/QLoRA 的 `--model-path` 和 `--base-model-path` 均传基础 Qwen3.5
模型，`--adapter-path` 指向这次实验的 `best_model/`；全参模式将 `--model-path` 指向 `best_model/`。训练使用
`enable_thinking=False`，推理也保持非思考模式。

```bash
python main_local.py --model-mode lora --base-model-path F:/model/Qwen3.5-0.8B --model-path F:/model/Qwen3.5-0.8B --adapter-path exp/sft_qwen3_5_lora/best_model --no-enable-thinking --result exp/sft_qwen3_5_lora/inference.json
```

`main_local.py` 的结果路径默认已有数据；实际运行前另设 `--result`，避免不同实验的推理结果混写。训练脚本不会改动现有在线结果、图片或已有推理程序。

参考 [Transformers 的 Qwen3.5 模型说明](https://huggingface.co/docs/transformers/main/model_doc/qwen3_5)
与 [多模态 SFT 指南](https://huggingface.co/docs/trl/main/training_vlm_sft)；训练代码直接使用项目本地安装的 Qwen3.5
processor/模型，不依赖 TRL。
