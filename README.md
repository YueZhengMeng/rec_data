# REC-8K 可复用细粒度子集流水线

这套代码在 REC-8K 原始标注的基础上，构建 **少量、可跨图复用、可人工审核**的
细粒度谓词子集，供后续互补预测与子集对比学习使用。

> `old/test`、`old/`、`v1/` 是历史遗留目录，本次重构不依赖也不修改它们。
> 本项目不在数据侧实现训练 loss；对比学习和互补预测在下游训练代码中实现。

## 为什么重构

旧流程把每个实例的开放描述直接变成候选词，再枚举所有 yes 条件交集，容易得到：

- 场景全局属性（如所有目标都在同一个盒子里）；
- 与父属性同义的词（如 `green pen → green barrel`）；
- 只适用于一张图的细粒度词（如 `below the 9 key`）；
- unknown 被当作 no 或失败被永久当成 unknown；
- 大量重叠交集，而不是适合学习的互补子集。

新流程从原始 train 标注建立受控词表，用 few-shot 引导但不强迫开放提取闭集化，
再按规则 → embedding → LLM 尾部归一化。子集阶段要求 yes/no 两侧都足够大、
unknown 足够少、条件之间不高度冗余，并把三值分区完整保存。最后用本地浏览器人工
审核通过门禁的第一层候选。

## 步骤总览

```text
step1_data_preprocess.py       原始 annotations.json → instance_data.jsonl
step2_build_vocabulary.py      train 标注 → attribute_vocabulary.json / .md
step3_extract_attributes.py    引导式开放提取 → extract_result.jsonl
step4_normalize_predicates.py  三级归一化 → predicate_candidates.json
step5_judge_predicates.py      每实例 yes/no/unknown → predicate_labels.jsonl
step6_build_subsets.py         门禁 + 三值分区 → fine_grained_subsets.jsonl
step7_review_sheet.py           本地浏览器审核 → decisions / final 子集
step8_visualize.py              三色点可视化
```

旧脚本名仍保留为薄包装，避免已有命令悄悄运行旧逻辑：

```text
step2_extract_attributes.py       → step3_extract_attributes.py
step3_propose_predicates.py       → step4_normalize_predicates.py
step4_judge_predicates.py         → step5_judge_predicates.py
step5_subset_partition.py         → step6_build_subsets.py
step5_visualize_fine_grained_subsets.py → step8_visualize.py
```

共享实现位于 `recsub/`。根目录的 `config.py`、`crop.py`、`prompts.py`、`utils.py`
等也只保留兼容导入层，新代码不要再从根目录导入。

## 首次准备

配置 `recsub/config.py` 中的默认值，或用环境变量覆盖：

```bash
# Windows PowerShell
$env:DASHSCOPE_API_KEY = "..."
$env:REC_IMAGE_DIR = "F:/REC-8K/rec-8k/"
$env:REC_MODEL = "qwen3.8-flash"
$env:REC_EMBEDDING_MODEL = "F:/model/Qwen3-Embedding-0.6B"
```

所有脚本从项目根目录运行。Windows 控制台由脚本自动切换 UTF-8。

## 运行流程

### 1. 实例表（JSONL）

```bash
python step1_data_preprocess.py
```

输出一行一个实例。`instance_id` 顺序保持旧版完全一致（286621 条时可逐条比对），
后续步骤可以流式读取，不再把 108MB 的实例表反复整体写回。

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

原始词的归一化是保守的。含 `first/second/third/leftmost/rightmost/front row/number N`
等场景相对词仍保留在词表和统计中，但默认不进入 few-shot。

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

### 3. 引导式开放提取

```bash
# 小批提示词检查
python step3_extract_attributes.py --image-names 0000-fsc147-1106.jpg,0001-fsc147-1306.jpg --no-thinking

# 每类别抽样 200 个实例
python step3_extract_attributes.py --sample-per-category 200 --seed 13 --no-thinking

# 全量
python step3_extract_attributes.py
```

提示词参考词表按 `(类别, 父槽位)` 注入，并排除当前父属性及别名；但模型仍可输出词表
没有的开放短语。每字段最多 2 条，允许空字段。`anno/extract_prompt_preview.json`
保存每个父槽位的一份提示词，人工可检查 few-shot 是否泄漏父属性。

结果 JSONL 每条含：

```json
{
  "instance_id": 0,
  "image_name": "...",
  "caption": "person standing",
  "category": "person",
  "attributes": {
    "appearance": [],
    "action": [
      "..."
    ],
    "location": [],
    "relation": []
  },
  "input_tokens": 1000,
  "output_tokens": 200,
  "failed": false
}
```

失败记录不会计入完成集合，重跑会自动重试。`--no-thinking` 用于实测成本/质量
消融，不能预先假设关闭后一定更好。

已有旧的 `anno/extract_result.json` 也可以迁移为 JSONL：

```bash
python -m recsub.io migrate --from anno/extract_result.json --to anno/extract_result_legacy.jsonl --expect-count 286621
python -m recsub.io migrate --from anno/instance_data.json --to anno/instance_data.jsonl --expect-count 286621
```

迁移出的 `extract_result_legacy.jsonl` 只有旧版 `more_attributes` 布局，可以直接作为
step4 的 `--input`；它 **不能**作为 step3 新格式的续跑文件。step3 如需重跑请使用
默认的 `anno/extract_result.jsonl`（不存在旧迁移文件时）或显式指定一个新文件，如
`--output anno/extract_result_v2.jsonl`。

### 4. 归一化

```bash
python step4_normalize_predicates.py --no-llm-tail
python step4_normalize_predicates.py
```

三级顺序：

1. 精确 / 别名 / 保守规则；
2. 同类别 embedding（阈值 `0.86`，有 margin，且经过颜色、方向、否定、维度、
   施动者、关系锚点、containment 等互斥面）；
3. LLM 尾部批量返回 `match` / `new` / `drop`。

`white laces` 不会因为与 `white` 相似就被吃掉；它被识别为细化，交给尾部决定是
生成新的可复用谓词还是丢弃。`outside the blister pack`、`near a blister pack`
不会再误合并进 `in the blister pack`。

候选格式按图片/caption 保存规范 `predicate_id`，同时保留 `raw_phrases`：

```json
{
  "predicates": [
    {
      "predicate_id": "person/action/walking",
      "display": "walking",
      "source": "reused",
      "merge_method": "exact",
      "raw_phrases": [
        "walking",
        "walking on the side"
      ]
    }
  ],
  "dropped": [
    {
      "phrase": "...",
      "reason": "parent_redundant"
    }
  ]
}
```

LLM 新造的谓词另存 `vocabulary_generated.json`，可直接做「仅原词表」与「加生成词」
的消融。

### 5. 三值判断

```bash
python step5_judge_predicates.py --no-thinking
python step5_judge_predicates.py --probe-per-caption 6 --no-thinking
```

只有看清相关部位、且明确不成立时才是 `no`；小、遮挡、边界、锚点不明都是
`unknown`。调用/解析/裁剪失败写 `judgment_failed: true`，不计入完成集合，重跑自动
重试。

`--probe-per-caption N` 先对每个根的 N 个实例判断，yes 比例 ≥0.98 或 ≤0.02 的
根×条件会被记录为恒定条件并剪掉。探针中存活条件的标签会复用，不会白花 token。

### 6. 门禁与三值子集

```bash
python step6_build_subsets.py
# 需要少量二阶交集时
python step6_build_subsets.py --max-depth 2
```

默认门禁：

- `min_support=5`、`min_complement=5`；
- `unknown_ratio <= 0.10`；
- `balance=min(yes,no)/parent >= 0.15`；
- 同根支持集 Jaccard 不超过 `0.80`；
- 每根最多 3 个谓词；
- 可为每个通过谓词生成 `not predicate` 的互补兄弟节点。

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

### 7. 浏览器人工审核

```bash
# 从 step6 输出构建审核条目与 CSV
python step7_review_sheet.py --build

# 本地服务（只绑定 127.0.0.1）
python step7_review_sheet.py --serve --reviewer r1

# 四人分片示例，START:END 是左闭右开
python step7_review_sheet.py --serve --reviewer r1 --shard 0:3249
python step7_review_sheet.py --serve --reviewer r2 --shard 3249:6498
```

浏览器里用 `K` keep、`D` discard、`U` 全标 unknown、`M` 改显示词、空格下一条、
`B` 上一条。每次决策立即追加到 `review/decisions_<reviewer>.jsonl`，中断可续审。

多人结束后：

```bash
python step7_review_sheet.py --ingest \
  review/decisions_r1.jsonl review/decisions_r2.jsonl \
  --final-output anno/fine_grained_subsets_final.jsonl
```

冲突投票不会静默按 discard 处理，会标为 `disagreement`，并输出 pairwise Cohen's
kappa。只有所有有记录的评审都 `keep` 才进入最终文件。

### 8. 可视化

```bash
python step8_visualize.py --image-names 0001-fsc147-1306.jpg
```

每页最多 40 个谓词，点按 yes/no/unknown 三色画，侧栏给规范谓词、计数、原始短语和
审核状态。缺图默认跳过。

## 重要边界

- 词表默认只从 train 构建；人工审核/伪标签增强只作用于训练侧。原始 REC-8K 的
  val/test 查询与分布不能因为「容易划分」而删除。
- `unknown` 不能补成 `no`；判断失败不能补成 `unknown`。
- 子集之间默认允许重叠；互补预测只能使用一条谓词的 yes/no 分区，不能把多个
  重叠子集的计数相加。
- `predicate_id` 由类别、维度、取值、动词和极性决定；不要直接用自由文本作为
  下游分类标签。
