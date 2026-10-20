# REC-8K 可复用细粒度子集流水线

这套代码在 REC-8K 原始标注的基础上，构建 **少量、可跨图复用、可人工审核**的
细粒度谓词子集，供后续互补预测与子集对比学习使用。

> `old/` 是历史实验目录，不属于本流水线，也没有任何步骤导入它。
> 本项目不在数据侧实现训练 loss；对比学习和互补预测在下游训练代码中实现。

## 设计要点

- **提取不带词表**。step3 只给类别与已知属性，让模型开放式描述目标；词表在
  step4 的归一化阶段才介入，避免开放式提取被词表规模限制。
- **词表只从 train 划分构建**，避免 val/test 的标注词进入提示词。
- **候选分四层预算**：同类相似词表词 → 跨类别相似词表词 → 同类不相似词表词 →
  LLM 尾部。前三层有视觉证据，最后一层只表示「值得问」。
- **判断保留三值**。只有看清目标相关部位且明确不成立才判 `no`；小、遮挡、
  边界、锚点不明一律 `unknown`，且 `unknown` 不会被补成 `no`。
- **子集过门禁**：yes/no 两侧都要够大、unknown 足够少、条件之间不高度冗余，
  三值分区完整保存，被拒绝的提案连同实际数值写入决策日志。

## 步骤总览

```text
step1_data_preprocess.py            原始 annotations.json → instance_data.jsonl
step2_build_vocabulary.py           train 标注 → attribute_vocabulary.json / .md
step3_extract_attributes_online.py  无词表开放式提取（在线）→ extract_result_online.jsonl
step3_extract_attributes_local.py   同一 prompt 的本地 Qwen 版本 → extract_result_local.jsonl
step4_normalize_predicates.py       归一化及四类预算 → predicate_candidates.jsonl
step5_judge_predicates_online.py    每实例 yes/no/unknown（在线）→ predicate_labels.jsonl
step5_judge_predicates_local.py     同一 prompt 的本地 Qwen 版本 → predicate_labels_local.jsonl
step6_build_subsets.py              门禁 + 三值分区 → fine_grained_subsets.jsonl
step7_visualize.py                 step6 正向子集可视化（每图一个子集）
step8_review_sheet.py               本地浏览器审核 → decisions / final 子集
```

共享实现位于 `recsub/`；根目录的 `fix_jsonl_format.py` 与
`convert_predicate_candidates.py` 是一次性数据修复脚本，不属于流水线步骤。
所有默认值集中在 `recsub/config.py`，命令行参数只覆盖单次运行。

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

在线请求统一使用 Chat Completions 的三张 JPEG `image_url` data URL。默认关闭思考；需要时在命令行加 `--enable-thinking`
。DeepSeek 使用官方视觉模型和 Chat Completions 接口时，保持当前消息格式，不要改成 Responses API 的 `input_image` 格式。

`recsub/config.py` 的段落与消费者一一对应：`online`（在线端点与并发/重试/超时）、
`local`（本地 Qwen 的模型与生成参数）、`paths`（全部产物路径）、`extract`（step3
抽样与预览）、`crop`（三视图裁剪，全流程共用一套）、`vocabulary`、`normalize`、
`judge`（step5 探针与恒定条件）、`gates`、`review`、`visualize`。

所有脚本从项目根目录运行。Windows 控制台由脚本自动切换 UTF-8。

### 在线采样默认值

`CONFIG["online"]["sampling"]` 按 provider 和思考模式设置，step3/step4 沿用官方默认；step5 仅对 Qwen 覆盖
`presence_penalty=0.0`，DeepSeek 不发送该覆盖。本地模型参数不变。

| 模型 / 模式                    |    temperature |  top_p |  top_k | presence_penalty | repetition_penalty |
|--------------------------------|---------------:|-------:|-------:|-----------------:|-------------------:|
| Qwen3.8-Flash 非思考（默认）   |            0.7 |    0.8 |     20 |              1.5 |                1.0 |
| Qwen3.8-Flash 思考（视觉输入） |            0.6 |   0.95 |     20 |              0.0 |                1.0 |
| deepseek-flash 非思考（默认）  |            1.0 | 不发送 | 不发送 |           不发送 |             不发送 |
| deepseek-flash 思考            | 不生效，不发送 | 不发送 | 不发送 |           不发送 |             不发送 |

- Qwen
  各模式的共享采样值采用[官方 API 默认值](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
  ；step5 仅将 Qwen 的 `presence_penalty` 覆盖为 `0.0`，以避免判断输出中的候选短语受到重复抑制；其他 Qwen 参数仍使用官方对应模式默认值。
- DeepSeek 仅非思考模式显式使用 `temperature=1.0`
  ，这是[官方“数据抽取/分析”的推荐值](https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings/)
  。按[官方思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)，思考模式下 temperature
  不生效，因此不发送；其他采样参数仍使用服务端默认，不发送 top_p=0.95 或 presence_penalty 覆盖。
- 采样设置同时纳入 step3/step5 续跑指纹；改参数后建议使用新输出文件及 `--check-fingerprint`
  ，避免混用结果。未做在线质量调优，这些是有文档依据的起始设置，不保证跨请求完全确定。

## 运行流程

### 1. 实例表（JSONL）

```bash
python step1_data_preprocess.py
```

输出一行一个实例，字段为
`image_name, caption, category, attributes, point, type, instance_id, image_idx,
caption_idx, point_idx`。`instance_id` 按「图片 → caption → 点」递增编号，后
续步骤可以流式读取，不必把 108MB 的实例表反复整体写回。

如果只验证前几张图：

```bash
python step1_data_preprocess.py --limit-images 10 --output anno/instance_data_10.jsonl
```

### 2. 标注词表

```bash
python step2_build_vocabulary.py --split train
```

默认只用 train 根，避免 val/test 的标注词泄漏。词表条目有：

- `category / dimension / value`；
- `verb`（例如 `walking` 与 `driving` 不混合）；
- `polarity`（positive / negative）；
- `scope`（absolute / scene_relative）；
- `root_count / instance_count / aliases / surfaces`。

原始词归一化保持保守。含 `first/second/third/leftmost/rightmost/front row/number N`
等场景相对词保留在统计中，但 step4 不会把它们当作「同类不相似」的补充候选。

人工覆盖可创建 `anno/vocabulary_overrides.json`：

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

重新运行 step2 即可。`attribute_vocabulary.md` 是人工浏览版本。

### 3. 开放式提取（在线／本地）

```bash
# 在线：先做小样本，默认每类别抽 2 个；0 表示全量
python step3_extract_attributes_online.py --image-names 0000-fsc147-1106.jpg --no-enable-thinking
python step3_extract_attributes_online.py --sample-per-category 0

# 本地：依赖 torch、accelerate、transformers、peft、bitsandbytes 和对应模型权重
python step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 2
accelerate launch step3_extract_attributes_local.py --model-path F:/model/Qwen3.5-0.8B --sample-per-category 0
```

两个本地入口都在每张 GPU 上加载一份完整模型副本做数据并行推理：`--batch-size` 是
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
改变 rank 数会改变随机数流，不保证逐 token 一致。两个本地入口的续跑指纹都绑定
进程数及每卡 batch；改变后建议指定新的 `--output`，以免混用结果。CUDA 集合通信推荐在
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

默认分别写 `anno/extract_result_online.jsonl` 与 `anno/extract_result_local.jsonl`。
成功记录带 `instance_id/image_name/caption/category/attributes`，失败记录带
`failed: true` 和原因且不会被计为已完成。运行指纹是整次运行的常量，不逐行重复，
而是旁挂一份 `anno/extract_result_*.jsonl.run.json`。step3/step5 在线和本地入口的续跑策略统一由
`CONFIG["resume"]["check_fingerprint"]` 控制，默认 **False**：打印警告后继续，允许当前模型、
prompt、裁剪、候选或实例表等配置与历史结果不同。成功 ID 仍跳过、失败 ID 仍重试。

- `--check-fingerprint`：显式启用严格检查，已有非空结果的指纹不同或标记缺失即拒绝续跑。
- `--no-check-fingerprint`：显式跳过指纹检查（也是当前默认行为），但 **不跳过**结果 JSONL 格式、
  现有记录结构或抽样清单校验；step5 探针模式也仍不能对部分结果续跑。
- 跳过时不改写已有 `.run.json`（包括损坏标记），非空结果缺标记时也不补写。标记不代表
  混合结果中的每条记录都来自同一配置；需要配置可追溯的实验应启用检查并使用新的 `--output`。
- 此次 Global 缩放改变输入及指纹；继续旧结果不会重算已成功的实例，文件可能包含新旧尺度的结果。

### 在线 batch 进度与逐条写入

step3/step5 在线入口当前 `--batch-size` 默认 2000，表示每批提交的实例数；
`--concurrency` 控制同时进行的 API 请求数（当前也为 2000），两者可分别覆盖。
每批显示独立的进度条，包含 batch 编号、完成数/总数、耗时和速度；
尾部剩余不超过 5 条时显示 `pending_ids`，可据此定位拖慢该批的实例。

普通提取/判断每返回一条结果，就立即追加一行 JSONL 并 flush，不必等整批完成；
进度在该条写入后更新，输出顺序是完成顺序而非 `instance_id` 顺序。
step5 的探针轮也显示进度，但最终标签需等探针剪枝完成后才能写入；
探针后的其余实例仍逐条即时写入。逐行 flush 不等于每条执行 `fsync`。

八卡 Bash/Linux 续跑示例（保留已有模型路径和生成参数，按需补充）：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 accelerate launch --multi_gpu --num_processes 8 \
  step3_extract_attributes_local.py --sample-per-category 0 --no-check-fingerprint
```

本地 step3 **和** step5 都默认保存四项指标，不需要开启逐 token 明细：

```json
{"input_tokens": 1119, "output_tokens": 98, "average_confidence": 0.778, "ppl": 1.495}
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
{"instance_id": 0, "image_name": "...", "caption": "person standing", "category": "person", "attributes": {"appearance": ["blue coat"]}, "input_tokens": 1119, "output_tokens": 98, "average_confidence": 0.778, "ppl": 1.495}
```

`--no-enable-thinking` 用于实测成本/质量消融，不预设关闭后一定更好。抽样清单、prompt 预览
按后端分开保存；续跑时先从完整数据抽相同样本，再排除成功 ID。

如果手上是记录数组形式的 JSON 提取产物（例如 `exp/` 下已有的实验结果），先转换到
独立 JSONL，再指定 step4 输入：

```bash
python -m recsub.io migrate --from exp/exp_local_qwen35_2B/result.json --to anno/extract_result_imported_local.jsonl --expect-count 286621
python step4_normalize_predicates.py --input anno/extract_result_imported_local.jsonl --no-embedding --no-llm-tail
```

这类记录只有 `instance_id` 和属性，step4 会按 `instance_id` 与完整
`anno/instance_data.jsonl` 归并配对。它们的字段与 step3 的产物不同，因此不能与
新格式的结果混在同一个文件里，也不能当作 step3 的续跑文件。

### 4. 归一化与根级候选预算

```bash
python step4_normalize_predicates.py                 # 默认无 LLM tail
python step4_normalize_predicates.py --llm-tail      # 启用 LLM 尾部来生成新谓词
```

多卡可用独立并行入口按完整图片分片（关闭 LLM tail）：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 python step4_normalize_predicates_parallel.py \
  --gpus 0,1,2,3 --input anno/extract_result_local.jsonl --no-llm-tail
```

每张卡启动一个 step4 进程，候选结果与生成词表在所有 worker 成功后合并；失败时会保留
临时分片和日志。默认成功后删除临时文件，可传 `--keep-temp` 保留。该入口当前不支持
`--llm-tail`，因为 tail 批次和类别级生成上限会因分片而变化。Windows PowerShell 可先设置
`$env:CUDA_VISIBLE_DEVICES="0,1,2,3"` 再运行 Python 命令。

step4 按 `anno/splits.json` 的 `(图片名, caption)` 根选择归一化词表：训练集使用
`anno/attribute_vocabulary_train.json`，val/test 共用 `anno/attribute_vocabulary_all.json`。
默认路径可分别用 `--vocabulary`、`--vocabulary-all` 和 `--splits` 覆盖，例如：

```bash
python step4_normalize_predicates.py --input anno/extract_result_local.jsonl \\
  --vocabulary anno/attribute_vocabulary_train.json \\
  --vocabulary-all anno/attribute_vocabulary_all.json --splits anno/splits.json \\
  --no-embedding --no-llm-tail
```

每个待处理根必须恰好属于 train/val/test 之一；未列入划分或重复划分会报错。切换词表策略后，
需重跑 step4，并重新生成依赖其候选的 step5 及后续产物；已有候选不会自动更新。

step4 先按根对开放短语做字符串与向量去重，再在当前根对应的人工审核词表里匹配；
词表不同写法只预编码一次并缓存在归一化器中；同图所有 caption 的开放短语
合并后以最多 256 条为一批编码，图片处理完清理临时向量（即使提取文件乱序也按图归并）。
如果入选候选与同图其他 caption 的原标注/开放属性 **字符串相同**（忽略大小写、
空白和首尾标点），会打印并丢弃该候选，在根的 `dropped` 中记下 ID、原因及
匹配到的其他 caption；语义向量相似只打印提醒，不改变候选。候选按 **同类相似词表词 → 跨类别相似词表词 → 同类不相似词表词 →
LLM 尾部**的顺序
分配预算。默认每根最多 30 个（`CONFIG["normalize"]["max_candidates_per_root"]`），
可用 `--max-candidates-per-root N` 覆盖，`0` 表示不发送候选；候选预算只此一处。
跨类别候选沿用其原词表
`predicate_id` 与 `category`（来源类别），根对象的 `category` 仍是目标类别。
同类不相似词表词只表示“值得问”，没有开放提取证据，不能预先当成 `no`。
内部选词时保留优先级与预算排名，紧凑输出只保留四类候选和字符串重复的
`dropped`；移除字符串重复候选后不会再从预算外补位。
本地结果可显式指定：

```bash
python step4_normalize_predicates.py --input anno/extract_result_local.jsonl \
  --max-candidates-per-root 30
```

`white laces` 不会因为与 `white` 相似就被吃掉；它被识别为细化，交给尾部决定是
生成新的可复用谓词还是丢弃。`outside the blister pack`、`near a blister pack`
不会再误合并进 `in the blister pack`。

候选格式按图片/caption 保存四类候选（step5/6 会自动展平）：

```json
{
  "category": "person",
  "parent_predicate_id": "person/action/walking",
  "candidates": {
    "same_category_similar": [{"predicate_id": "person/action/sitting", "display": "sitting", "dimension": "action", "value": "sitting"}],
    "cross_category_similar": [],
    "same_category_dissimilar": [],
    "llm_tail": []
  },
  "dropped": [{"predicate_id": "person/color/blue", "phrase": "blue", "reason": "cross_caption_string_duplicate", "detail": "'blue person' 的属性 'blue'"}]
}
```

LLM 新造的谓词另存 `vocabulary_generated.json`，可直接做「仅原词表」与「加生成词」
的消融。

### 5. 三值判断

```bash
# 在线
python step5_judge_predicates_online.py --no-enable-thinking
python step5_judge_predicates_online.py --probe-per-caption 6 --no-enable-thinking

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
重试。

step5 使用 step4 每根的 `predicates` 列表逐实例判断；候选文件改变（包括预算、词表或输入
版本）时必须使用新的标签输出路径。运行指纹写在输出旁的旁挂标记
`anno/predicate_labels.jsonl.run.json` 里（一次运行一个值，逐行重复没有意义），
续跑时比对标记，不符或有记录却缺标记都会报错，不会静默复用其他候选的已完成实例；
删除标记等于放弃续跑。`--probe-per-caption` 在已有部分结果后不会重算剪枝，
需新建输出文件。被探针剪掉的「根 × 恒定条件」写入
`anno/predicate_constant.json`（本地为 `predicate_constant_local.json`）。

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

`unknown` 始终单独保存，不会补成 `no`。含 unknown 时，yes/no 仅在已知实例中互补，
完整父集计数应满足 `parent = yes + no + unknown`，不能直接使用 `parent = yes + no`。
另外，当前 balance 按完整父集计算，要求 yes/no 各占至少 10%，因此 unknown 超过 80%
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

### 7. 子集可视化

```bash
# 每张 PNG 只展示一个正向子集；互补子集不生成图片
python step7_visualize.py --image-names 0001-fsc147-1306.jpg
```

直接读取 step6 的 `fine_grained_subsets.jsonl`（无需 step1 实例表）。原始类别、原始
caption、新增用于划分子集的属性都显示在图片右侧；图片标题只保留流程和子集编号。
红叉标记当前子集正样本，绿点标记同根 caption 中其余实例（根实例集减去当前子集，
包括 unknown；仅作显示分组，不把 unknown 改成 no）。互补节点不单独生成图片。
默认输出到 `show_subsets/positive/`，文件名包含 step6 的稳定 `node_id`。缺图默认跳过。

### 8. 本地单人浏览器审核

```bash
# 从 image_idx=0 开始；首次运行自动构建条目、启动 loopback 浏览器并打开审核页
python step8_review_sheet.py

# 从指定原始图片索引开始（image_idx 来自 step1 实例表）
python step8_review_sheet.py --image-idx 120

# 可选：只构建审核条目与 CSV，不启动界面
python step8_review_sheet.py --build
```

界面一次展示一个子集，原图右侧使用大号粗体、不同强调色的信息卡展示**原始类别、原始 caption、新增划分属性**，
并直接显示 yes/no/unknown 数量、框选点数和保留/废弃状态；完整 JSON 默认折叠在“查看技术详情”中。
顶部同时显示 source `image_idx`、图片名和当前审核进度。`image_idx` 是 step1 按原始图片顺序从 0 开始编号的索引；如果指定索引没有子集，则从后续第一个有可审子集的图片开始。← / → 或
A / D 切换上一/下一子集，不改变保留状态；空格显式废弃当前子集并前进。右侧状态横幅用黄色“未审核 · 默认保留”、绿色“已审核 · 保留”或红色“已审核 · 已废弃”区分审核进度和去留。没有废弃记录的子集
默认保留。鼠标拖框结束后会立即将所选 yes/no 点各翻转一次，不需要额外点击；如果还要再次翻转，可点“手动翻转所选 yes/no”。快捷键 W 将框选点全部设为 yes，S 全部设为 no；两个操作也会把框选到的 unknown 设为指定标签。状态和点标签每次变更立即追加保存到
`review/review_state.jsonl`，首次启动时该文件不存在也可正常审核，首次保存更改会自动创建；已有文件不会被清空，
中断后再次运行会恢复审核状态。关闭进程时会导出最终数据，界面中的
“保存最终子集”按钮也可随时导出；最终文件默认为 `anno/fine_grained_subsets_final.jsonl`，
只排除显式废弃的子集，并将人工修改后的 yes/no/unknown 实例分区写回。

服务只绑定 `127.0.0.1`，是单机单人界面；不提供 reviewer、分片或多人合并流程。可用
`--instances`、`--image-dir`、`--review-dir`、`--state` 和 `--final-output` 覆盖输入输出路径。

## 重要边界

- 词表默认只从 train 构建；人工审核/伪标签增强只作用于训练侧。原始 REC-8K 的
  val/test 查询与分布不能因为「容易划分」而删除。
- `unknown` 不能补成 `no`；判断失败不能补成 `unknown`。
- 子集之间默认允许重叠；互补预测只能使用一条谓词的 yes/no 分区，不能把多个
  重叠子集的计数相加。
- `predicate_id` 由类别、维度、取值、动词和极性决定；不要直接用自由文本作为
  下游分类标签。
- 所有由模型或人工伪标记得来的子集都不是金标准，step6 输出统一带
  `review.status=pending`。
