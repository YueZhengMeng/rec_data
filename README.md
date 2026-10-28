# REC-8K 可复用细粒度子集流水线

这套代码在 REC-8K 原始标注的基础上，构建 **少量、可跨图复用、可人工审核**的
细粒度谓词子集，供后续互补预测与子集对比学习使用。

> 本项目不在数据侧实现训练 loss；对比学习和互补预测在下游训练代码中实现。

## 设计要点

- **提取不带词表**。step3 只给类别与已知属性，让模型开放式描述目标；词表在
  step4 的归一化阶段才介入，避免开放式提取被词表规模限制。
- **词表按划分隔离**。step4 对 train 根使用 `--split train` 构建的词表，对
  val/test 根使用 `--split all` 的全量词表；step3 的开放提取 prompt 不含词表。
- **候选分三层预算**：同类相似词表词 → 跨类别相似词表词 → 同类不相似词表词。
  前两层有视觉证据，最后一层只表示「值得问」。
- **判断保留三值**。只有看清目标相关部位且明确不成立才判 `no`；小、遮挡、
  边界、锚点不明一律 `unknown`。模型判断与门禁阶段不会把 `unknown` 补成 `no`；
  人工审核完成后如何汇总见第 8 节。
- **子集过门禁**：yes/no 两侧都要够大、unknown 足够少、条件之间不高度冗余，
  三值分区完整保存，被拒绝的提案连同实际数值写入决策日志。

## 步骤总览

```text
step1_data_preprocess.py            原始 annotations.json → instance_data.jsonl
step2_build_vocabulary.py           标注 → attribute_vocabulary_all.json / .md（--split train 另生成训练词表）
step3_extract_attributes_online.py  无词表开放式提取（在线）→ extract_result.jsonl
step3_extract_attributes_local.py   同一 prompt 的本地 Qwen 后端 → extract_result_local.jsonl
step4_normalize_predicates.py       归一化及三类预算 → predicate_candidates.jsonl
step5_judge_predicates_online.py    每实例 yes/no/unknown（在线）→ predicate_labels.jsonl
step5_judge_predicates_local.py     同一 prompt 的本地 Qwen 后端 → predicate_labels_local.jsonl
step6_build_subsets.py              门禁 + 三值分区 → fine_grained_subsets.jsonl
step7_review_sheet.py               本地浏览器审核 → review_state / final 子集
step8_merge_review_results.py       审核结果合并 + 人工分区复验门禁 → final_subsets.jsonl
step9_compose_captions_online.py    原属性 + 新划分属性 → 新短语/caption + 两项检查（在线）
step9_compose_captions_local.py     同一套纯文本 prompt 的本地 Qwen 后端
step10_review_captions.py           历史候选人工选用 / 编辑属性短语并确认 yes
step11_export_rec8k.py              仅导出独立子集标注（带 root_caption），不写划分文件
step12_review_overlaps.py           本地浏览器审核互补子集中的共享坐标，仅保存审核状态
step12_apply_overlap_reviews.py     应用已确认共享点并仅去重 annotations_subset.json（默认 dry-run）
step13_final_audit.py               最终审计与报告；可选删除未通过等价门禁的互补 pair
```

共享实现位于 `recsub/`；根目录的 `fix_jsonl_format.py` 用于修复外部 JSON/JSONL
格式，不属于流水线步骤：

```bash
python fix_jsonl_format.py                      # 递归修复当前目录下所有 .jsonl
python fix_jsonl_format.py --no-recursive       # 只处理当前目录一层
python fix_jsonl_format.py anno --dry-run       # 只统计指定目录，不写文件
```

它把「缩进 JSON 首尾相接」「顶层数组」等写法统一成一行一条，默认从当前目录递归搜索
（跳过 `.git`、`.idea` 等隐藏目录），原地原子替换；已经是标准 JSONL 的文件默认跳过，
`--force` 可强制重写。影响数据与模型行为的默认值集中在 `recsub/config.py`，命令行参数
只覆盖单次运行。

失败记录分离脚本 `step3_split_failed_extractions.py`、`step5_split_failed_judgments.py` 和
`step9_split_failed_captions.py` 会原子地把待重跑记录移出成功结果；
`step4_normalize_predicates_parallel.py` 提供按图片分片的多 GPU 归一化入口。

## 环境与依赖

需要 Python 3.10 或更高版本。核心依赖（在线路径与数据处理）：

```bash
pip install -r requirements.txt
```

- `openai`：在线 Chat Completions 调用；
- `Pillow`：三视图裁剪、标记与图片编码；
- `tqdm`：在线 batch 进度。

本地 Qwen 后端额外需要 `torch`、`transformers`、`accelerate`、`peft` 和
`bitsandbytes`，并准备对应的模型权重；`--model-mode nf4` 还需要可用的 CUDA GPU。
嵌入匹配需要 `Qwen3-Embedding` 权重，并在 `REC_EMBEDDING_MODEL`（或
`CONFIG["normalize"]["embedding_model"]`）中指定本地目录。

## 首次准备

配置 `recsub/config.py` 中的默认值，或用环境变量覆盖：

```bash
# Windows PowerShell
$env:REC_PROVIDER = "qwen"       # qwen 或 deepseek
$env:DASHSCOPE_API_KEY = "..."   # REC_PROVIDER=qwen
$env:DEEPSEEK_API_KEY = "..."    # REC_PROVIDER=deepseek
$env:REC_API_KEY = "..."         # 可选；优先于上面的 provider 专用 key
$env:REC_MODEL = "qwen3.8-flash"  # deepseek 时可用 deepseek-flash
$env:REC_BASE_URL = "..."         # 可选；按 provider 有默认值
$env:REC_IMAGE_DIR = "F:/REC-8K/rec-8k/"
$env:REC_EMBEDDING_MODEL = "F:/model/Qwen3-Embedding-0.6B"
$env:REC_LOCAL_MODEL = "F:/model/Qwen3.5-0.8B"
```

```text
REC_PROVIDER=qwen      → DashScope / qwen3.8-flash
REC_PROVIDER=deepseek  → DeepSeek / deepseek-flash
```

在线请求统一使用 Chat Completions 的三张 JPEG `image_url` data URL。默认关闭思考；需要时在
命令行加 `--enable-thinking`。DeepSeek 使用官方视觉模型和 Chat Completions 接口时，保持当前
消息格式，不要改成 Responses API 的 `input_image` 格式。

`recsub/config.py` 的段落与消费者一一对应：顶层 `provider`/`api_key`/`base_url`/
`model_name`/`image_dir`（在线端点）、`online`（并发/重试/超时与采样）、`local`
（本地 Qwen 的模型与生成参数）、`paths`（全部产物路径）、`extract`（step3 抽样与
预览）、`resume`（续跑指纹策略）、`crop`（三视图裁剪，全流程共用一套）、
`vocabulary`、`normalize`、`gates`、`review`、`caption`。

所有脚本从项目根目录运行。Windows 控制台由脚本自动切换 UTF-8。

### 默认产物路径

以下路径都来自 `CONFIG["paths"]`，可用各脚本的 `--output`、`--input` 等参数覆盖单次运行。

| `paths` key                              | 默认文件                                                                    | 用途                                |
|------------------------------------------|-----------------------------------------------------------------------------|-------------------------------------|
| `instance_data`                          | `anno/instance_data.jsonl`                                                  | step1 实例表，后续所有步骤的父点集  |
| `vocabulary_all`                         | `anno/attribute_vocabulary_all.json`                                        | val/test 根使用的全量词表           |
| `vocabulary_train`                       | `anno/attribute_vocabulary_train.json`                                      | train 根使用的训练词表              |
| `extract_result_online` / `_local`       | `anno/extract_result.jsonl` / `anno/extract_result_local.jsonl`             | step3 开放提取结果                  |
| `predicate_candidates_online` / `_local` | `anno/predicate_candidates.jsonl` / `anno/predicate_candidates_local.jsonl` | step4 归一化候选                    |
| `predicate_labels_online` / `_local`     | `anno/predicate_labels.jsonl` / `anno/predicate_labels_local.jsonl`         | step5 三值判断                      |
| `subsets`                                | `anno/fine_grained_subsets.jsonl`                                           | step6 门禁后的三值子集              |
| `subset_decisions`                       | `anno/subset_decisions_log.jsonl`                                           | step6 拒绝提案与实际数值            |
| `review_dir`                             | `review/`                                                                   | step7 审核状态与任务目录            |
| `subsets_final`                          | `anno/fine_grained_subsets_final.jsonl`                                     | step7 单人审核的最终子集            |
| `caption_subsets`                        | `anno/final_subsets.jsonl`                                                  | step8 汇总结果，也是 step9 默认输入 |
| `captions_online` / `_local`             | `anno/subset_captions.jsonl` / `anno/subset_captions_local.jsonl`           | step9 合成文本与检查结果            |
| `review_subset_decisions`                | `anno/review_subset_decisions_log.jsonl`                                    | step8 门禁拒绝与末行汇总            |
| `annotations_subset`                     | `anno/annotations_subset.json`                                              | step11 独立导出标注                 |

step12/step13 使用脚本内的独立默认路径：审核状态在 `review/overlaps/`，应用报告为
`anno/step12_apply_overlap_report.json`，最终审计报告为
`anno/step13_final_audit_report.json`；step12 可用 `--review-dir`、`--report` 覆盖，
step13 可用 `--report` 覆盖。

### 在线采样默认值

`CONFIG["online"]["sampling"]` 按 provider 和思考模式设置，step3 采用官方默认；step5 与
step9 仅对 Qwen 覆盖 `presence_penalty=0.0`，DeepSeek 不发送该覆盖。本地模型参数不变。

| 模型 / 模式                    |    temperature |  top_p |  top_k | presence_penalty | repetition_penalty |
|--------------------------------|---------------:|-------:|-------:|-----------------:|-------------------:|
| Qwen3.8-Flash 非思考（默认）   |            0.7 |    0.8 |     20 |              1.5 |                1.0 |
| Qwen3.8-Flash 思考（视觉输入） |            0.6 |   0.95 |     20 |              0.0 |                1.0 |
| deepseek-flash 非思考（默认）  |            1.0 | 不发送 | 不发送 |           不发送 |             不发送 |
| deepseek-flash 思考            | 不生效，不发送 | 不发送 | 不发送 |           不发送 |             不发送 |

- Qwen
  各模式的共享采样值采用[官方 API 默认值](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)；
  step5 与 step9 仅将 Qwen 的 `presence_penalty` 覆盖为 `0.0`，以避免判断或合成输出中的候选短语
  受到重复抑制；其他 Qwen 参数仍使用官方对应模式默认值。
- DeepSeek 仅非思考模式显式使用 `temperature=1.0`
  ，这是[官方“数据抽取/分析”的推荐值](https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings/)。
  按[官方思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)，思考模式下
  temperature 不生效，因此不发送；其他采样参数仍使用服务端默认，不发送 top_p=0.95 或
  presence_penalty 覆盖。
- 采样设置同时纳入 step3/step5/step9 续跑指纹；改参数后建议使用新输出文件并加
  `--check-fingerprint`，避免混用结果。未做在线质量调优，这些是有文档依据的起始设置，
  不保证跨请求完全确定。

## 运行流程

### 1. 实例表（JSONL）

```bash
python step1_data_preprocess.py
```

输出一行一个实例，字段为
`image_name, caption, category, attributes, point, type, instance_id, image_idx,
caption_idx, point_idx`。`instance_id` 按「图片 → caption → 点」递增编号，后
续步骤可以流式读取，不必把整张实例表反复整体写回。

如果只验证前几张图：

```bash
python step1_data_preprocess.py --limit-images 10 --output anno/instance_data_10.jsonl
```

### 2. 标注词表

```bash
# 全量词表（val/test 根使用）
python step2_build_vocabulary.py --split all

# 训练词表（train 根使用）
python step2_build_vocabulary.py --split train \
  --output anno/attribute_vocabulary_train.json \
  --markdown anno/attribute_vocabulary_train.md
```

`--split` 默认 `all`，默认输出 `anno/attribute_vocabulary_all.json` 与
`anno/attribute_vocabulary_all.md`；也接受 `train`/`val`/`test`。`--split train`
只统计训练根，避免 val/test 的标注词进入训练侧词表；用 `--output`/`--markdown`
写到单独的 train 文件。
`--overrides` 默认指向 `anno/vocabulary_overrides_all.json`，训练词表需要另一份
覆盖文件时也要显式指定。词表条目有：

- `category / dimension / value`；
- `verb`（例如 `walking` 与 `driving` 不混合）；
- `polarity`（positive / negative）；
- `scope`（absolute / scene_relative）；
- `root_count / instance_count / aliases / surfaces`。

原始词归一化保持保守。含 `first/second/third/leftmost/rightmost/front row/number N`
等场景相对词保留在统计中，但 step4 不会把它们当作「同类不相似」的补充候选。

人工覆盖可创建 `anno/vocabulary_overrides_all.json`：

```json
{
  "relabel": {
    "person/location/in-queue": {
      "value": "in the queue"
    }
  },
  "merge": {
    "person/clothing/blue-dress": "person/clothing/dress"
  },
  "aliases": {
    "person/accessory/umbrella/verb=holding": [
      "has an umbrella"
    ]
  },
  "drop": [
    "person/location/one-off-layout-value"
  ]
}
```

重新运行 step2 即可。`attribute_vocabulary_all.md` 是人工浏览用词表；训练词表对应
`attribute_vocabulary_train.md`。

### 3. 开放式提取（在线／本地）

```bash
# 在线：默认每类抽样 0（全量）；先用小样本试跑时显式传入抽样数
python step3_extract_attributes_online.py --image-names 0000-fsc147-1106.jpg --no-enable-thinking
python step3_extract_attributes_online.py --sample-per-category 2
python step3_extract_attributes_online.py --sample-per-category 0

# 本地：依赖 torch、accelerate、transformers、peft、bitsandbytes 和对应模型权重
python step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 2
accelerate launch step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 0
```

`--image-names` 接受逗号分隔的图片名，step4/step5/step9 的同名参数沿用相同写法；只处理
匹配的图片，续跑仍按各自输出文件判断已完成项。

step3 与 step5 的本地入口都在每张 GPU 上加载一份完整模型副本做数据并行推理：`--batch-size` 是
每张 GPU 的 batch size，四进程配 `--batch-size 16` 时全局 batch 最多为 64；rank
之间只通过 `gather_object` 汇总结果，只有主进程写文件。`--model-mode nf4` 是
bitsandbytes NF4 4-bit 权重、按 `--dtype`（默认 bf16）计算；`--model-mode full`
是非量化完整权重的同精度推理，并不是 fp32，也不是跨 GPU 模型切分。推理模型不会
交给 `Accelerator.prepare()`/DDP。

四卡 Bash/WSL2/Linux 启动示例：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 accelerate launch --multi_gpu --num_processes 4 \
  step3_extract_attributes_local.py --model-mode nf4 --batch-size 16 --sample-per-category 0
```

PowerShell 请使用 `$env:CUDA_VISIBLE_DEVICES="0,1,2,3"` 后再运行
`accelerate launch ...`；原生命令行的 `CUDA_VISIBLE_DEVICES=... command` 是 Bash
语法。若要比较不同进程数的确定性输出，使用 `--no-do-sample`；采样模式默认开启，
改变 rank 数会改变随机数流，不保证逐 token 一致。三个本地入口（step3/step5/step9）的
续跑指纹都绑定进程数及每卡 batch；改变后建议指定新的 `--output`，以免混用结果。CUDA 集合通信推荐在
Linux/WSL2 环境运行，不能仅通过修改 PowerShell 语法就保证原生 Windows 多卡可用。

两端从 `recsub/prompts.py` 使用同一份提取文本与类别引导，提取 prompt **不包含
词表**；密度索引一律用完整实例表，裁剪配置也一律取 `CONFIG["crop"]`。step3 与 step5 的
online/local 四个入口共用同一 cropper：Global 使用与下游
`T.RandomResize([800], max_size=1333)` 相同的等比例缩放及整数尺寸规则，允许放大
（例如 384×384 → 800×800、2000×1000 → 1332×666）；配置为 `full_short_side=800`、
`full_max_size=1333`。遵循 DETR 的整数舍入顺序，极长图的长边可能略超 1333
（例如 1000×5000 → 267×1335），不是另外强制截断。只缩放 Global，不改写源图片；Context/Fine 仍从原图裁剪并各输出
512×512。Global/Context 在各自输出尺寸上绘制 outline 标记，点和裁剪框仍以原图坐标保存。
各阶段 online/local 的文本、裁剪配置及 Global→Context→Fine→文本顺序相同，但在线传
JPEG base64、本地传 PIL/processor 输入，图像编码不是逐字节相同；本地 processor 还可能
进行 patch 尺寸对齐，因此不保证最终模型 tensor 与下游任务完全相同。

默认分别写 `anno/extract_result.jsonl`（在线）与 `anno/extract_result_local.jsonl`。
成功记录带 `instance_id/image_name/caption/category/attributes`，失败记录带
`failed: true` 和原因且不会被计为已完成。运行指纹是整次运行的常量，不逐行重复，
而是旁挂一份 `anno/extract_result.jsonl.run.json` 或
`anno/extract_result_local.jsonl.run.json`。step3/step5/step9 的在线和本地入口共用同一套
续跑策略，由 `CONFIG["resume"]["check_fingerprint"]` 控制，默认 **False**：打印警告后继续，
允许当前模型、prompt、裁剪、候选或实例表等配置与已有结果不同。成功 ID 仍跳过、失败 ID 仍重试。

- `--check-fingerprint`：显式启用严格检查，已有非空结果的指纹不同或标记缺失即拒绝续跑。
- `--no-check-fingerprint`：显式跳过指纹检查（也是当前默认行为），但 **不跳过**结果 JSONL 格式、
  现有记录结构或抽样清单校验。
- 跳过时不改写已有 `.run.json`（包括损坏标记），非空结果缺标记时也不补写。标记不代表
  混合结果中的每条记录都来自同一配置；需要配置可追溯的实验应启用检查并使用新的 `--output`。
- Global 视图的缩放参数同时进入输入和运行指纹；续跑只跳过已成功的实例，不会重算它们。

失败记录可用 `step3_split_failed_extractions.py` 原子分离，输入文件只保留成功记录：

```bash
python step3_split_failed_extractions.py --input anno/extract_result.jsonl
# 本地结果
python step3_split_failed_extractions.py --input anno/extract_result_local.jsonl
```

默认失败文件为输入名加 `_failed`（例如 `anno/extract_result_failed.jsonl`），也可用
`--failed-output` 指定。分离后的失败文件可作为重跑或人工排查清单，不要直接当作
step3 的续跑输出。

### 在线 batch 进度与逐条写入

step3/step5/step9 在线入口的 `--batch-size` 默认 2500，表示每批提交的实例数；
`--concurrency` 控制同时进行的 API 请求数（默认也为 2500），两者可分别覆盖。
每批显示独立的进度条，包含 batch 编号、完成数/总数、耗时和速度；
批次末尾剩余不超过 5 条时显示 `pending_ids`，可据此定位拖慢该批的实例。

普通提取/判断每返回一条结果，就立即追加一行 JSONL 并 flush，不必等整批完成；
进度在该条写入后更新，输出顺序是完成顺序而非 `instance_id` 顺序。
逐行 flush 不等于每条执行 `fsync`。

八卡 Bash/Linux 续跑示例（保留已有模型路径和生成参数，按需补充）：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 accelerate launch --multi_gpu --num_processes 8 \
  step3_extract_attributes_local.py --sample-per-category 0 --no-check-fingerprint
```

本地 step3 和 step5 默认在记录顶层保存四项指标，不需要开启逐 token 明细；step9 的同类
指标分别保存在 `generation_usage` 与 `review_usage` 中：

```json
{
  "input_tokens": 1119,
  "output_tokens": 98,
  "average_confidence": 0.778,
  "ppl": 1.495
}
```

- `input_tokens`：每条输入 attention mask 的有效 token 数（包括图像占位/模板，不含 batch PAD）。
- `output_tokens`：实际生成 token 数，含首个 EOS、不含其后的 PAD；thinking 模式含思考 token。
- `average_confidence`：这些 token 的概率算术均值。
- `ppl`：`exp(-平均 log p)`，是生成序列的困惑度，不是标注答案的 teacher-forcing 困惑度。

`ConfidenceRecorder` 只保留上一步的一份 `[batch, vocab]` logits，获取已生成 token
的概率后累积标量统计并释放该向量；`generate` 显式禁用
`output_scores/output_logits/output_hidden_states/output_attentions`，即使 checkpoint
开启它们也不会保存全序列。统计取的是应用 penalties 之后、temperature/top-k/top-p
采样之前的分布，因此不称为未处理的原始 logits。默认只保留均值和 PPL；
`--record-token-confidences` 另外保存 CPU 上的逐 token 概率及 ID，而非 logits。
解析失败时保留可用指标；裁剪或调用失败没有实际输出时不伪造指标。

在线结果保存 API 提供的 token 用量。step3 本地成功记录示例：

```json
{
  "instance_id": 0,
  "image_name": "...",
  "caption": "person standing",
  "category": "person",
  "attributes": {
    "appearance": [
      "blue coat"
    ]
  },
  "input_tokens": 1119,
  "output_tokens": 98,
  "average_confidence": 0.778,
  "ppl": 1.495
}
```

`--no-enable-thinking` 用于实测成本/质量消融，不预设关闭后一定更好。抽样清单、prompt 预览
按后端分开保存；续跑时先从完整数据抽相同样本，再排除成功 ID。

如果提取产物是记录数组形式的 JSON，先转换成一行一条的 JSONL，再指定 step4 输入：

```bash
python -m recsub.io migrate --from path/to/result.json --to anno/extract_result_imported_local.jsonl
python step4_normalize_predicates.py --input anno/extract_result_imported_local.jsonl --no-embedding
```

这类记录只有 `instance_id` 和属性，step4 会按 `instance_id` 与完整
`anno/instance_data.jsonl` 归并配对。它们的字段与 step3 的产物不同，因此不能与
step3 的结果混在同一个文件里，也不能当作 step3 的续跑文件。

### 4. 归一化与根级候选预算

```bash
python step4_normalize_predicates.py
```

多卡可用独立并行入口按完整图片分片：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 python step4_normalize_predicates_parallel.py \
  --gpus 0,1,2,3 --input anno/extract_result_local.jsonl
```

每张卡启动一个 step4 进程，候选结果在所有 worker 成功后合并；失败时会保留
临时分片和日志。默认成功后删除临时文件，可传 `--keep-temp` 保留。
Windows PowerShell 可先设置 `$env:CUDA_VISIBLE_DEVICES="0,1,2,3"` 再运行 Python 命令。

step4 按 `anno/splits.json` 的 `(图片名, caption)` 根选择归一化词表：训练集使用
`anno/attribute_vocabulary_train.json`，val/test 共用 `anno/attribute_vocabulary_all.json`。
默认路径可分别用 `--vocabulary`、`--vocabulary-all` 和 `--splits` 覆盖，例如：

```bash
python step4_normalize_predicates.py --input anno/extract_result_local.jsonl \
  --vocabulary anno/attribute_vocabulary_train.json \
  --vocabulary-all anno/attribute_vocabulary_all.json --splits anno/splits.json \
  --no-embedding
```

每个待处理根必须恰好属于 train/val/test 之一；未列入划分或重复划分会报错。切换词表策略后，
需重跑 step4，并重新生成依赖其候选的 step5 及后续产物；已有候选不会自动更新。

step4 先按根对开放短语做字符串与向量去重，再在当前根对应的人工审核词表里匹配；
词表不同写法只预编码一次并缓存在归一化器中；同图所有 caption 的开放短语
合并后以最多 256 条为一批编码，图片处理完清理临时向量（即使提取文件乱序也按图归并）。
如果入选候选与同图其他 caption 的原标注/开放属性 **字符串相同**（忽略大小写、
空白和首尾标点），会打印并丢弃该候选，在根的 `dropped` 中记下 ID、原因及
匹配到的其他 caption；语义向量相似只打印提醒，不改变候选。候选按 **同类相似词表词 → 跨类别相似词表词 → 同类不相似词表词**
的顺序分配预算。默认每根最多 30 个（`CONFIG["normalize"]["max_candidates_per_root"]`），
可用 `--max-candidates-per-root N` 覆盖，`0` 表示不发送候选；候选预算只此一处。
跨类别候选沿用其原词表
`predicate_id` 与 `category`（来源类别），根对象的 `category` 仍是目标类别。
同类不相似词表词只表示“值得问”，没有开放提取证据，不能预先当成 `no`。
内部选词时保留三层优先级，紧凑输出只保留三类候选和字符串重复的
`dropped`；移除字符串重复候选后不会再从预算外补位。
本地结果可显式指定：

```bash
python step4_normalize_predicates.py --input anno/extract_result_local.jsonl \
  --max-candidates-per-root 30
```

`white laces` 不会因为与 `white` 相似就被吃掉；它被识别为细化，保留为未解析短语。
`outside the blister pack`、`near a blister pack` 不会再误合并进
`in the blister pack`。

候选格式按图片/caption 保存三类候选（step5/6 会自动展平）：

```json
{
  "category": "person",
  "parent_predicate_id": "person/action/walking",
  "candidates": {
    "same_category_similar": [
      {
        "predicate_id": "person/action/sitting",
        "display": "sitting",
        "dimension": "action",
        "value": "sitting"
      }
    ],
    "cross_category_similar": [],
    "same_category_dissimilar": []
  },
  "dropped": [
    {
      "predicate_id": "person/color/blue",
      "phrase": "blue",
      "reason": "cross_caption_string_duplicate",
      "detail": "'blue person' 的属性 'blue'"
    }
  ]
}
```

### 5. 三值判断

```bash
# 在线
python step5_judge_predicates_online.py --no-enable-thinking

# 本地：4 卡、每卡最多 16 个实例；先以较小 batch 验证显存
CUDA_VISIBLE_DEVICES=0,1,2,3 accelerate launch --multi_gpu --num_processes 4 \
  step5_judge_predicates_local.py --model-mode nf4 --batch-size 16
# 非量化完整微调权重，必须提供实际权重目录
CUDA_VISIBLE_DEVICES=0,1,2,3 accelerate launch --multi_gpu --num_processes 4 \
  step5_judge_predicates_local.py --model-mode full --model-path /path/to/checkpoint \
  --batch-size 16 --no-do-sample
```

两个入口用同一份判断 prompt，只有推理后端不同。本地结果写入独立的
`anno/predicate_labels_local.jsonl`，供 step6 用
`--labels anno/predicate_labels_local.jsonl` 读取。

只有看清相关部位、且明确不成立时才是 `no`；小、遮挡、边界、锚点不明都是
`unknown`。调用/解析/裁剪失败写 `judgment_failed: true`，不计入完成集合，重跑自动
重试。需要把失败记录移出成功结果时运行：

```bash
python step5_split_failed_judgments.py --input anno/predicate_labels.jsonl
# 本地结果
python step5_split_failed_judgments.py --input anno/predicate_labels_local.jsonl \
  --failed-output anno/predicate_labels_local_failed.jsonl
```

该脚本同时分离 `judgment_failed` 与 `judgment_missing`，原文件原子替换为成功记录。

step5 使用 step4 每根的 `candidates` 列表逐实例判断；候选文件改变（包括预算、词表或输入
配置）时必须使用新的标签输出路径。运行指纹写在输出旁的旁挂标记
`anno/predicate_labels.jsonl.run.json` 里（一次运行一个值，逐行重复没有意义）。
默认 `CONFIG["resume"]["check_fingerprint"]=False`，续跑只打印警告；显式加
`--check-fingerprint` 后才会比对标记，不符、缺标记或记录与标记冲突都会报错，不会静默
复用其他候选的已完成实例。关闭校验时不读取、不改写既有标记。

### 6. 门禁与三值子集

```bash
python step6_build_subsets.py
# 需要少量二阶交集时
python step6_build_subsets.py --max-depth 2
```

默认门禁（`CONFIG["gates"]`）：

- `min_support=2`、`min_complement=2`；
- `unknown_ratio=unknown/parent <= 0.90`，仅在超过 90% 时由 unknown 门禁拒绝；
- `balance=min(yes,no)/parent >= 0.10`；
- 同根支持集 Jaccard 不超过 `0.80`；
- 每根最多 3 个谓词；
- 可为每个通过谓词生成 `not predicate` 的互补兄弟节点。

step6 阶段 `unknown` 始终单独保存，不会补成 `no`。含 unknown 时，yes/no 仅在已知实例中互补，
完整父集计数应满足 `parent = yes + no + unknown`，不能直接使用 `parent = yes + no`。
另外，balance 按完整父集计算，要求 yes/no 各占至少 10%，因此 unknown 超过 80%
时即使通过 90% 的 unknown 上限，也会被平衡度门禁拒绝。

节点显式保留三值：

```json
{
  "node_type": "predicate_subset",
  "predicate": {
    "predicate_id": "...",
    "display": "walking"
  },
  "instance_ids": [
    "yes"
  ],
  "negative_instance_ids": [
    "no"
  ],
  "unknown_instance_ids": [
    "unknown"
  ],
  "counts": {
    "parent": 20,
    "yes": 9,
    "no": 10,
    "unknown": 1
  },
  "review": {
    "status": "pending"
  }
}
```

`subset_decisions_log.jsonl` 记录每条未通过提案及门禁实际数值。不要把兄弟子集当成
互斥类别；只有 yes/no/unknown 三者合计覆盖父集时才可做互补一致性检查。

### 7. 本地单人浏览器审核

```bash
# 首次运行自动构建条目，从 image_idx=0 开始审核
python step7_review_sheet.py

# 本次只审核 image_idx=1000～1999；区间左闭右开，导航不会越界
python step7_review_sheet.py --start 1000 --end 2000

# 后续使用已构建条目，恢复同一区间的审核状态
python step7_review_sheet.py --serve --start 1000 --end 2000

# 只设置初始游标：从 120 开始，但不限制向前/向后浏览的范围
python step7_review_sheet.py --image-idx 120

# 可选：只构建全量审核条目与 CSV，不启动界面
python step7_review_sheet.py --build
```

`--start` 默认 0，`--end` 默认不限；索引必须非负，终点必须大于起点。如果指定起点没有子集，
则从区间内后续第一个有可审子集的图片开始；空区间会报错，不启动页面。
`--image-idx` 仅指定初始游标，默认使用本次区间起点；与区间参数同时使用时，游标必须在区间内。

界面一次展示一个子集，原图右侧使用大号粗体、不同强调色的信息卡展示 **原始类别、原始 caption、新增划分属性**，
并直接显示 yes/no/unknown 数量、框选点数和审核状态；完整 JSON 默认折叠在“查看技术详情”中。
顶部显示本次审核区间、source `image_idx`、图片名和子集进度。`image_idx` 来自 step1，按原始图片顺序从 0 编号。
同一图片的多个子集全部纳入区间，但 **每个子集独立保存审核状态**，不会因同图某一子集已审核而跳过其他待审核子集。

| 状态                      | 界面区分                         | 含义                                           |
|---------------------------|----------------------------------|------------------------------------------------|
| `pending`                 | 灰色虚线，○ 未审核 · 不导出结果  | 尚未完成审核，仅展示或跳过不算审核操作         |
| `keep`，`defaulted=true`  | 绿色实线，✓ 已审核 · 默认保留   | 审核人员查看后按 D / →，接受原标签并进入下一条 |
| `keep`，`defaulted=false` | 绿色实线，✓ 已审核 · 已确认保留 | 点击“确认保留”或在普通未审核条目上修改点标签   |
| `discard`                 | 红色实线，✕ 已审核 · 已废弃     | 显式废弃，不进入最终数据                       |
| `unknown`                 | 蓝色实线，？ 不确定 · 待二次审核 | 点击“不确定”，保存后留在当前子集               |

**→ / D** 先将当前未审核子集保存为“已审核 · 默认保留”（`status="keep"`、`reviewed=true`、`defaulted=true`），
保存成功后才前进；保存失败停留原处。下一条尚未审核的子集仍为 pending，不会提前标记已审核。
在区间最后一条按 D / → 也会保存审核结果，但不越界。← / A 只返回上一子集，不改变状态；空格显式废弃当前子集并前进。
已有保留、废弃或 unknown 状态不会因按 D / → 被覆盖；点击“确认保留”会改为明确保留并清除默认保留标记。
**“跳过已审核”**从下一子集向后扫描，跳过所有已审核保留（包括按下一条默认保留）/废弃，停在下一条 unknown 或未审核子集；不绕回，
无后续待审核条目时提示并保留当前位置，也不会修改被跳过的条目。
unknown 后继续编辑点标签仍保持 unknown，直到点击“确认保留”或“废弃”。

**标记显示控制**：点击“标记大小”按钮在 `standard`（标准大小）和 `small` 之间切换；
`small` 的红叉长宽、绿点直径及对应描边均为标准的一半。点击“隐藏标记（F）/显示标记（F）”按钮，
或按 **F / f**，可切换红叉和绿点的显示。默认标准大小、显示标记；翻页和图片加载不会重置本次页面的设置，刷新页面后恢复默认。
这些操作只改变显示，不提交审核状态、不改点标签或任务 fingerprint。隐藏标记时仍可拖框审核，黄色选中圈和拖框保留原尺寸；
输入框内的 F、Ctrl/Cmd+F 等组合键不会触发标记切换。

鼠标拖框结束后立即将所选 yes/no 点各翻转一次；可点“手动翻转所选 yes/no”再次翻转。
W 将框选点全部设为 yes，S 全部设为 no；这两个操作也会把框选到的点级 unknown 设为指定标签。 **审核状态 unknown 与实例点的
unknown 分区相互独立**，“不确定”按钮不修改实例标签。
状态和点标签每次变更立即保存到 `review/review_state.jsonl`，首次保存自动创建文件。
文件按 `review_id` 保存 **最新状态快照**，同一子集只有一条记录，重复审核会替换已有结果，而不是追加历史行。
写入采用临时文件、fsync 和原子替换，成功后才更新内存；写入失败不推进审核，也不覆盖之前的状态。
仅保存经过操作的 keep/unknown/discard，pending 不写入状态文件；废弃记录保留在状态文件中，以便恢复和跳过。
状态行优先使用显式 `status`；没有 `status` 时按 `discard`/`reviewed`/`labels` 推断。
已有追加式日志仍可读取，同一 ID 取最后一条；下次成功保存、手动导出或正常退出时自动整理为唯一记录，
若最后一条为 pending 则移除该记录，不会恢复其更早的保留决策。仅启动/展示页面不重写已有文件。

关闭进程时会导出最终数据，“保存最终子集”也可随时导出，默认为 `anno/fine_grained_subsets_final.jsonl`。 **只导出经过审核操作且当前为
keep/unknown 的子集**，没有操作的 pending 和已废弃的 discard 不进入最终子集文件。
按 D / → 接受的条目属于已操作子集，正常导出为 `review.status="keep"`、`review.defaulted=true`、`review.reviewed=true`；
明确确认保留或修改点标签的条目同样导出。不确定条目标记 `review.status="unknown"`、`review.defaulted=false`、
`review.reviewed=false`，留待二次审核。
分段审核仍检查完整输入集合与全量审核状态，保留其他区间 **已经操作过**的结果，不补入未审核条目。
每次导出按 `review_id` 去重并原子重建文件，同一子集只保留最新状态和点标签，不会残留已废弃的行；
同图不同子集独立保留。导出提示同时显示已操作保留、废弃、未审核未导出的数量。
审核准备文件 `review_items.jsonl` 和 CSV 仍包含完整待审核集合，它们是输入清单，不是审核结果。

服务默认只绑定 `127.0.0.1`（`CONFIG["review"]["host"]`），是单机单人界面；不提供 reviewer 或多人协同编辑。普通模式
默认使用 `review/review_items.jsonl`、`review/review_state.jsonl` 和
`anno/fine_grained_subsets_final.jsonl`，可用 `--instances`、`--image-dir`、`--review-dir`、`--state` 和
`--final-output` 覆盖输入输出路径。

### 7.1 多人审核任务自动准备与版本校验

如果由多名同学分工审核，先由负责人基于同一份 `subsets` 和 `instances` **只构建一次冻结任务包**：

```bash
# 生成 8 个任务：review_01=[0,1000)，review_02=[1000,2000)，……
python step7_review_sheet.py --prepare-tasks 8 --images-per-task 1000 \
  --review-dir review_batch
```

也可以指定起始图片索引：

```bash
python step7_review_sheet.py --prepare-tasks 8 --images-per-task 1000 \
  --start 1000 --review-dir review_batch
```

准备命令只调用一次审核条目构建流程，然后复制同一份 `review_items.jsonl` 和 CSV 到每个任务目录，生成：

```text
review_batch/
├── review_batch.json          # 批次总清单、dataset_id、8 个区间
├── review_01/
│   ├── review_manifest.json   # 任务 ID、区间、数量、清单 SHA-256、fingerprint
│   ├── review_items.jsonl     # 与其他任务字节完全相同的冻结清单
│   ├── review_sheet.csv
│   ├── review_state.jsonl     # 审核后产生：本任务最新状态
│   └── final_subsets.jsonl    # 审核后产生：本任务结果
└── review_02/ … review_08/
```

每位同学只需要使用自己任务目录启动，不需要再提供 `subsets`、`instances` 或手动填写区间：

```bash
python step7_review_sheet.py --review-dir review_batch/review_02 \
  --image-dir <本机图片目录>
```

程序发现 `review_manifest.json` 后会自动：

- 读取任务目录中的冻结清单，不重新构建、不调用本机的上游数据；
- 从 manifest 恢复该任务的 `[start, end)` 区间；
- 校验 `review_items.jsonl` 的 SHA-256、`dataset_id`、`review_id` 唯一性、`image_idx` 映射、子集数量和任务 fingerprint；
- 校验已有状态的 `review_id` 和实例标签属于当前任务；
- 默认把状态和最终结果写回当前任务目录。

因此，只要 8 个任务由同一次 `--prepare-tasks` 生成，8 位同学看到的 `review_id`、子集内容和图片索引就来自同一份冻结清单。
即使有人修改了 `review_items.jsonl`、混入其他任务的状态、或使用了不同版本清单，启动时也会报错并且不会启动浏览器或覆盖结果。
复制任务目录到其他电脑不会改变版本指纹；只需另行提供与 `image_name`
匹配的原图。任务仍按图片索引分配，区间内一张图片的多个子集归同一个任务，实际子集数量以启动前的任务统计为准。

审核完成后，应将整个 `review_XX` 目录交回，而不是只交 `final_subsets.jsonl`。其中 `review_state.jsonl` 还包含废弃和
unknown，后续整合脚本可用 `review_id` 检查是否漏审、重复或冲突。不要让两位同学共享同一任务目录或同时写同一个状态文件。

冻结任务目录不允许使用 `--build`、替换 `--items`、把 `--state` 或 `--final-output` 写到目录外。
准备批次时，`--review-dir` 可以是不存在的新目录或已有空目录；非空目录（包括已有任务或审核结果）仍拒绝覆盖。
请显式指定 `--review-dir review_batch`，否则使用配置中的默认 `review` 目录。

### 8. 审核结果汇总与人工分区复验

审核任务全部完成后，运行汇总脚本把审核结果合并至 step9 默认输入，并依据人工修正后的三值分区重新应用 step6 门禁：

```bash
python step8_merge_review_results.py
```

默认读取 `review/review_batch.json` 中的全部任务，检查批次/任务 fingerprint、状态覆盖、keep 状态与 final
结果一致性、实例分区完整且互斥；缺少审核、仍有 `pending/unknown` 或结果冲突时会报错，不写输出。只依赖审核文件，无需重新读取
step4 候选或 step5 模型标签。

- **沿用 G3、G6**：审核结果是 step6 通过集的一部分，谓词身份未变、每根数量只会减少，不重复执行父谓词冗余过滤或数量筛选。
- **复验 G1、G5**：按人工修改后的 yes/no/unknown 列表重新统计支持度和平衡度。
- **跳过 G2**：审核规范要求审核员将应为 yes 的实例全部标记为 yes。门禁仍按人工三值分区计算，不改变既有 G1/G4/G5
  筛选结果；通过门禁后，将剩余 unknown 统一归入 no，使否定集合严格等于 root − yes。此规则仅适用于人工审核完成后的汇总，不改变
  step5/step6 的三值判断规则。
- **复验 G4**：人工改标签可能使原本不同的支持集高度重叠；沿用 step6 的排序和 Jaccard 判定，在同根内重新去冗余。
- 仅对 keep 的正向子集过门禁；每个通过的正集随后生成一条补集，不生成深度 2 交集。正集 ID 不变；补集使用与 step6 一致的稳定
  ID，以 `complement_of` 关联正集，`predicate.display = "not " + 正集 predicate.display`，谓词 ID 加 `__COMPLEMENT` 后缀、极性为
  `negative`。补集的正成员为根集合减去审核 yes；正负节点的 unknown 均清空，并同步更新 yes/no/other 坐标、计数和平衡度。
  `post_review_unknown_as_no_instance_ids` 记录此次归入 no 的原 unknown 成员，原审核任务文件及人工标签覆盖记录不改动。例如
  `wearing a hat` 对应 `not wearing a hat`。
- 补集从人工审核后的分区推导，不读取 step6 的补集成员、不独立参与 G4/G6 筛选。保留审核来源，在 `review.derived_from`
  标明推导关系，点级 `label_overrides` 同步取反。每对节点按“正集 → 补集”相邻写出；日志区分门禁后正集数、补集数及总输出节点数。

成功后写入 `anno/final_subsets.jsonl`（step9 默认输入），覆盖原有汇总；拒绝原因、实测数值、阈值、来源及末行汇总写入
`anno/review_subset_decisions_log.jsonl`，原审核任务文件不变。用 `--dry-run` 可先完整校验并统计而不写文件。门槛默认沿用
`recsub/config.py` 的 `gates`，可用 `--min-support`、`--min-complement`、`--min-balance`、`--max-support-jaccard` 覆盖；输出路径可用
`--output` 和 `--decisions` 指定。

### 9. 子集属性短语与 caption 合成（在线／本地）

step9 默认输入当前合并后的 `anno/final_subsets.jsonl`，也可用 `--subsets` 指定其他最终
子集文件，例如 `anno/fine_grained_subsets_final.jsonl`（step7 单人审核）或
`review/review_01/final_subsets.jsonl`（step7 任务目录）。原属性来自 `--instances` 实例表的
`attributes` 字段，按 `(image_name, caption)` 配对；新增属性使用
`predicate.display`（含动作、否定词或交集），不使用裸 `value`。类别以子集顶层的
`category` 为准，并检查它与父根一致，不使用跨类别复用词条里的 `predicate.category`。

整体分为三个阶段，只进行两次 LLM 调用；两段 prompt 位于 `recsub/prompts.py`，
online/local 共用：

1. **合成属性短语（LLM）**：输入仅为 `category`、`original_attributes`、
   `additional_attribute`，输出仅为一行纯文本属性短语，不输出 JSON 或 caption。
   代码将回复保存为 `new_attributes`，并直接拼接
   `new_caption = category + " " + new_attributes`，类别原词不做任何变形。
   优先保留两组属性的连续原文，但允许必要的语法调整，不能丢失原意或否定条件。
2. **字符串检查（代码）**：检查两个输入属性字符串是否严格 `in new_attributes`，
   记录 `original_attributes_in_new`、`additional_attribute_in_new` 和综合
   `string_check` 为 `yes/no`。即使 `string_check=no`，仍进入第三阶段，不提前拒绝。
3. **合理性审核（LLM）**：输入仅为 `category`、`original_attributes`、
   `additional_attribute`、`new_attributes`、`new_caption` 五个字段，判断语言和语义
   是否大致合理，只输出 `yes/no`，存为 `language_check`。无需苛求表达完美；
   审核不接收 `string_check`，也不重新判断图片上的视觉事实。

`string_check` 使用大小写敏感的 Python `in`，不做词干化或模糊匹配；空原属性
不增加限制。它只是诊断项：必要语法调整可能使它为 `no`，仍可由语言审核判为
`yes`。`accepted=true` 表示生成格式正确且语言审核为 `yes`， **不保证视觉真实性**。
如需最保守的训练文本，可额外筛选 `accepted=true AND string_check="yes"`。

```bash
# 在线先试前 10 个子集；正式全量去掉 --limit 或设为 0
python step9_compose_captions_online.py --limit 10 --check-fingerprint
python step9_compose_captions_online.py --limit 0 --check-fingerprint

# 指定另一份最终子集；模型、端点、key 仍沿用 REC_PROVIDER/REC_MODEL 等现有配置
python step9_compose_captions_online.py --subsets review/review_01/final_subsets.jsonl \
  --output anno/review_01_captions.jsonl --check-fingerprint

# 本地复用现有 Qwen 模型加载、量化、生成与置信度统计，但只输入文本，不读取图片
python step9_compose_captions_local.py --model-path F:/model/Qwen3.5-0.8B --limit 10
accelerate launch step9_compose_captions_local.py --batch-size 8 --check-fingerprint

# 将成功记录留在原文件，把失败、未完成和审核 no 分离到 *_failed.jsonl
python step9_split_failed_captions.py
python step9_split_failed_captions.py --input anno/subset_captions_local.jsonl \
  --failed-output anno/subset_captions_local_failed.jsonl
```

每个子集正常需要两次 LLM 调用；生成格式无效时不会发起语言审核。在线继承已有
并发、重试与逐条追加写入，本地继承每卡完整模型副本、批失败逐条重试和主进程
按批写入。step9 的 presence penalty 默认覆盖为 `0.0`，便于复用属性原文
（在线仅 Qwen 发送；本地可用 `--presence-penalty` 覆盖）。不需要 `--image-dir`。

`step9_split_failed_captions.py` 可在 step9 中断或完成后清理结果：它原子地把原 JSONL
替换为仅含 `accepted=true` 且 `language_check="yes"` 的成功记录，并将失败、缺少审核
结果、属性短语解析失败和 `language_check="no"` 的完整原记录写入另一个
`*_failed.jsonl`。默认在线输入为 `anno/subset_captions.jsonl`；本地结果请显式指定
`--input anno/subset_captions_local.jsonl`。分离后的失败/no 文件可作为人工检查清单；
如需再次调用 step9，请使用原始最终子集输入和一个新的输出文件，或按续跑规则继续
使用原输出，不要把 `*_failed.jsonl` 直接当作 step9 的输出文件。

step9 默认输出为 `anno/subset_captions.jsonl` / `anno/subset_captions_local.jsonl`， **不覆盖原始子集文件，不改写原
`caption`、谓词、实例 ID 列表或点集**，而是增加字段：

```json
{
  "node_id": "pred_...",
  "caption": "person sitting",
  "category": "person",
  "original_attributes": "sitting",
  "additional_attribute": "in the right section",
  "new_attributes": "sitting in the right section",
  "new_caption": "person sitting in the right section",
  "original_attributes_in_new": "yes",
  "additional_attribute_in_new": "yes",
  "string_check": "yes",
  "language_check": "yes",
  "accepted": true
}
```

示例省略了保留的原子集元数据和两次调用各自的 `generation_usage/review_usage`；
顶层 `input_tokens/output_tokens` 为两次 LLM 调用合计。调用失败、属性短语输出
解析失败或审核输出不是 yes/no 的记录带 `failed=true` 和原因。续跑按 `node_id` 判断
（没有时用 `review_id`）。模型结果按最后一条判断，只有 `accepted=true` 且
`language_check="yes"` 才会跳过；人工确认结果优先于模型历史记录，详见第 10 节。
没有结果、失败、未完成审核和审核为 no 的条目会再次执行，模型新结果追加到同一 JSONL。
类别前缀由代码拼接保证，不依赖 LLM 生成。默认指纹策略与 step3/5 相同，建议加
`--check-fingerprint`；如果要用当前 prompt 重试已有结果而指纹不一致，可显式使用
`--no-check-fingerprint` 或指定新的 `--output`。`--limit` 先截取再跳过已完成项，
因此同样参数重跑仍只处理前 N 个子集。

### 10. 人工选择历史短语或编辑后确认 yes

LLM 多次生成仍未通过时，可运行本地浏览器审核页：

```bash
# 默认审核完整的 anno/subset_captions.jsonl，自动打开 http://127.0.0.1:8766/
python step10_review_captions.py

# 只校验、统计待审核条目与历史候选，不写文件、不启动服务
python step10_review_captions.py --check-only

# 本地模型结果
python step10_review_captions.py --input anno/subset_captions_local.jsonl
```

页面仅列出失败、no 或未完成审核的子集，展示类别、原属性、新增条件、当前结果和 **历次合成候选**。相同短语合并展示，但保留每次审核状态、失败原因及记录序号。
选用某种历史短语时默认选中该短语最近一次保存的记录；可继续编辑，也可跳过。

保存规则：

- **直接确认当前记录，或选择历史候选且不改文本**：原地更新选中的那一条记录，
  `language_check=yes`、`accepted=true`，不追加行、不改变其他记录内容。原地更新采用
  临时文件 + fsync + 原子替换，记录数量保持不变。
- **编辑或手动输入文本后确认**：新 caption 由类别原词 + 空格 + 新属性短语生成，
  作为新人工记录追加到同一文件，原记录不修改。`Ctrl+Enter` 可保存当前文本。
- 两种操作都会重新计算字符串检查；即使 `string_check=no` 也可由人工确认。
  不改动原属性、谓词、视觉审核状态或实例成员；保留人工确认来源及时间，`.run.json`
  保持不变。选用候选本身不会写文件，必须点击确认。

**人工确认优先于模型历史**：即使确认的是较早的一条记录，step9 续跑也会跳过该
子集，step11 会采用人工确认的记录，不会被文件后面的模型 no 覆盖。多条记录均经
人工确认时，以最近人工确认时间为准；没有人工确认时，模型记录仍按最后一条判断。

请直接审核完整结果文件，以保留全部历史并便于 step10 读取；不要同时运行其他
step9、分离脚本或另一个人工审核进程。默认仅绑定本机，端口可用 `--port` 覆盖。
页面刷新或脚本重启会自动跳过已通过的条目。审核完成后运行
`python step11_export_rec8k.py --dry-run`，确认后再正式导出；已有导出文件时需要
`--overwrite`。

### 11. 仅导出独立 REC-8K 子集标注

step11 不合并、不覆盖原始 `annotations.json`，仅生成 `anno/annotations_subset.json`：

- 只包含 step9 文本通过语言审核、且未被废弃的新增子集 caption，格式为
  `图片名 → 新 caption → {class, attribute, points, type, root_caption, subset_polarity, complement_caption}`。
- `root_caption` 标明原始根 caption；不保存 `subset_id`，不混入原始根标注。
- `subset_polarity` 为 `"yes"`（正集）或 `"no"`（负集，即本数据定义的 root − yes）。
  以最终子集节点的 `predicate.polarity` / `complement_of` 为准，不从短语措辞猜测身份。
- `complement_caption` 为 **同一图片**中对应另一侧的新 caption，正负双方互相引用。
  按 `complement_of` 建立关联，不按文本相似度匹配；若另一侧未导出，则为 `null`。
- 正负点集直接使用最终审核的 `instance_ids`，不重算 root − yes，不额外过滤 unknown。
- `type` 继承根 caption，图片顺序沿用原标注。
- 原始 `splits.json` 仅供只读校验和统计， **不修改、不生成任何 split 文件**。

导出前会校验原始划分确实是图片级划分：train/val/test 互不重叠，并覆盖原标注的全部
图片；同时从实例表按最终 `instance_ids` 回查点坐标，避免使用子集记录中的 `yes_points`。
默认只导出 `accepted=true` 且 `language_check=yes` 的记录；`string_check=no` 但语言
审核为 yes 的记录仍会导出。没有通过审核、调用失败或缺少 step9 结果的子集会跳过并
在终端统计。若同一图片内生成相同 caption，脚本拒绝覆盖或合并，避免丢失根 caption。

```bash
# 先做完整校验和统计，不写文件
python step11_export_rec8k.py --dry-run

# 默认使用在线 step9 结果；本地 step9 结果需显式指定
python step11_export_rec8k.py --captions anno/subset_captions_local.jsonl

# 输出已存在时必须明确允许替换；原始输入始终禁止作为输出
python step11_export_rec8k.py --overwrite
```

可用 `--annotations-output` 指定标注输出路径；不能指向任意输入文件。

### 12. 互补子集共享坐标人工审核与应用

同一坐标可能在互补 yes/no 两侧同时出现。浏览器工具只展示正集 caption 和共享点选择，负集 caption
不要求审核。正侧独有点显示红叉，负侧独有点显示绿点，共享点显示蓝色方框。选择为 yes 的共享点进入正集；未选择的点只有点击“确认并下一对”后才归入
no。未确认或仅保存草稿绝不会按 no 导出。

```bash
# 1. 启动本地审核；只写 review/overlaps/review_state.jsonl
python step12_review_overlaps.py

# 2. 审核完成后先预览；默认 dry-run，不写 annotations_subset.json
python step12_apply_overlap_reviews.py --dry-run

# 3. 确认预览后，生成 backup 并只写回 annotations_subset.json
python step12_apply_overlap_reviews.py --apply
```

默认绑定 `127.0.0.1:8768`。审核状态保存在 `review/overlaps/`；输入标注或子集文件变化时，
manifest 指纹不匹配会拒绝沿用既有审核状态。apply 阶段只修改
`anno/annotations_subset.json` 的 points，并对该文件每个 caption 的精确坐标稳定去重；
`anno/annotations.json` 始终只读。apply 默认备份到 `anno/annotations_subset.json.bak`，
已有备份时拒绝覆盖；审核结果已应用、当前没有共享坐标对时报告 `no_op`，不会重复改写。

### 13. 最终审计

step13 默认只读审计输入、仅写报告到 `anno/step13_final_audit_report.json`：重复坐标、
互补 pair 覆盖与共享点、等价 step8 门禁（G1/G4/G5）、同图同文本不同点集等。

```bash
# 只审计并写报告
python step13_final_audit.py

# 可选：删除未通过等价门禁的正/负互补 pair
python step13_final_audit.py --apply
```

`--apply` 在确有删除时先备份 `anno/annotations_subset.json`，再删除未通过等价门禁的
正/负互补 pair；没有失败 pair 时报告 `no_op`、不改写文件。它不会删除文本冲突记录，
也不会修改原始 `annotations.json`。

## 重要边界

- step3–step6 阶段 `unknown` 不能补成 `no`，判断失败也不能补成 `unknown`；只有
  step8 在人工审核完成后，按审核规范把剩余 `unknown` 归入 `no` 并显式记录成员。
- 子集之间默认允许重叠；互补预测只能使用一条谓词的 yes/no 分区，不能把多个
  重叠子集的计数相加。
- `predicate_id` 由类别、维度、取值、动词和极性决定；不要直接用自由文本作为
  下游分类标签。
- 所有由模型或人工伪标记得来的子集都不是金标准，step6 输出统一带
  `review.status=pending`。
