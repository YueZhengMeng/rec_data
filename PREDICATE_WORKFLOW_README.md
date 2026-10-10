# 自动闭集属性划分

三个脚本各自只负责一个步骤，没有人工审核环节：

1. `propose_predicates.py`：按 `(image_name, caption_idx)` 整理候选。原 caption 的目标点集是全集；先按字符数、单词数过滤，再用 embedding 去重，每个 caption 调用一次文本 LLM。
2. `judge_predicates.py`：把所有候选 caption 的实例摊平成一个列表，整体分批并发判断，只保存每个实例的 `yes/no/unknown` 三个列表；不生成子集、不调用子集脚本。按实例而不是按图片分批，实例少的图片不会浪费并发名额。
3. `subset_partition.py`：读取三值结果，使用 **yes 列表** 构造条件及条件交集，复用层次划分与点集输出逻辑。原实例表提供完整父集，不使用开放式提取的 `more_attributes` 作为划分依据。**子集之间允许互相重叠**：同一个点集只输出一次，但不同条件的子集不再互相排斥。
4. `visualize_fine_grained_subsets.py`：把划分结果按层渲染成 PNG，重叠点用同心标记表示。

## 运行

设置 `DASHSCOPE_API_KEY`，确认本地 embedding 权重、在线模型与图像目录。输出文件换新名字可避免混入旧实验结果。

```bash
python propose_predicates.py --input anno/extract_result.json --output anno/predicate_candidates.json --embedding-model F:/model/Qwen3-Embedding-0.6B --max-length 80 --max-words 4 --max-images 100
python judge_predicates.py --instances anno/instance_data.json --candidates anno/predicate_candidates.json --image-dir F:/REC-8K/rec-8k --output anno/predicate_labels_v2.json
python subset_partition.py --input anno/predicate_labels_v2.json --instances anno/instance_data.json --output anno/fine_grained_subsets_judged.json --min-subset-size 5 --max-condition-depth 3
python visualize_fine_grained_subsets.py --annotations anno/fine_grained_subsets_judged.json --image-dir F:/REC-8K/rec-8k --output-dir show_judged
```

`judge_predicates.py` 不再有 `--subsets-output` 参数；`--batch-size` 是每批并发判断的实例数，默认取 `config.process_batch_size`，`--max-images` 用于限制纳入判断的候选图片数（`0` 表示不限）。判断结果每批存盘，中断后重新运行只判断剩余实例。`subset_partition.py` 不再需要 `--labels`；`--input` 就是判断结果，默认 `anno/predicate_labels.json`。新命令入口不加载 embedding，已判定的短语不再做语义合并。旧算法的函数接口保留用于历史测试，不是当前默认流程。

默认在线接口是 `https://dashscope.aliyuncs.com/compatible-mode/v1`，通过 `--model` / `--base-url` 修改。`--max-images 0` 表示不限图片；候选阶段的 `--batch-size` 是并发 caption 数。候选与判断脚本重复运行会分别跳过已完成 caption / 实例；已有判断结果如果还是旧的逐词 `labels` 字典，需更换输出文件重新运行，不能当作三个列表续跑。不要在同一个结果文件里混用不同模型或不同候选词表。

## 结果格式

### 候选属性

按图片和 caption 存储，只保存新增词列表：

```json
{
    "0000-fsc147-1106.jpg": {
        "red pen": ["white cap", "black band"],
        "blue pen": ["transparent cap"]
    }
}
```

内部按 `caption_idx` 分组，JSON 键使用 caption 文本。LLM 只看到类别和新增短语列表，不看到原标注、图片名、序号等冗余内容。原始属性参与 embedding 去重和输出排除，但不加入 prompt。

### 实例三值结果

每条记录保留原实例信息（`instance_id`、图片、caption、类别、原属性、点与索引等），增加三个列表及 token 用量，例如：

```json
[
    {
        "instance_id": 0,
        "image_name": "0000-fsc147-1106.jpg",
        "caption": "red pen",
        "category": "pen",
        "attributes": "red",
        "point": [155.36, 48.55],
        "image_idx": 0,
        "caption_idx": 0,
        "point_idx": 0,
        "yes": ["white cap"],
        "no": ["black band"],
        "unknown": [],
        "input_tokens": 1000,
        "output_tokens": 50
    }
]
```

漏项、无效值、调用失败进入 `unknown`；没有候选的已选 caption 实例也保存三个空列表，不调用 MLLM。`phrases_for_instance` 明确循环取当前 caption 的词，发现同词出现在同图其他 caption 的候选或文本里就打印重复提示；提示不把其他 caption 的词加入当前列表，也不删当前词。

### 子集结果

兼容 `annotations_format`：保留原 caption 的完整点集，并从 yes 属性建立层次子集。**不同条件的子集允许重叠**，例如「white laces」和「dark laces」可以共享同一批实例，两者的交集也会作为一个更深的子集输出；`parent_ids` 仍指向生成它的父节点，因此结果是一棵树而不是互斥划分。`no` 和 `unknown` 不进入正子集，不能把它们都称为互补集。输出新增子集带 `label_source: model_pseudo` 和 `complete`：若父集存在未判实例或相关候选仍为 unknown，则 `complete: false`，表示已确认的正部分，不声称完整属性子集。新流程不自动生成否定表达式或互补子集。

划分仍沿用原有最小子集规模与最大条件深度的限制，但去掉了原来的同层互斥筛选，条件交集只来自实例的 yes。自动标签不是人工金标准。

## 可视化

`visualize_fine_grained_subsets.py` 按「新增属性个数 + 1」分层，每层生成一张 PNG：左侧是原图与彩色标记，右侧图例给出每个子集的原属性、`more_attributes`、点数，以及与其他子集共享的点数；`complete` 为 false 的子集标注 `partial`。

一个点属于多个子集时，标记会**绕该点排成一圈**，每个标记保持完整的大小、颜色和形状（不再互相遮盖），并用细连线、中心小点和一圈浅色外框标出这一组属于同一个实例，因此能与相邻实例的单个标记区分开。图上出现一圈标记时，对应的就是图例里那几个子集的交集。缺图默认跳过并给出提示，`--strict` 改为直接报错；`--image-name` 可只渲染指定图片。

自动生成的图是**教师模型伪标签**的检视图，不是人工金标准。

## 离线测试

```bash
PYTHONIOENCODING=utf-8 python -m unittest test_predicate_workflow.py test_subset_partition.py test_crop.py test_online_attributes.py
```
