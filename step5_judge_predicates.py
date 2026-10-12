"""step5：逐实例闭集三值判断（原 step4）。

与原版的关键差别：

1. **失败与「看不见」分开**。原实现把调用失败/解析失败也写成 unknown 并计入
   已完成集合，重跑会直接跳过这些实例——判断损失被永久当成视觉 unknown。现在
   失败记录带 ``judgment_failed`` 标记，**不计入已完成集合**，重跑自动重试。
2. **用规范写法发问**。同一个谓词在所有实例上问法一致（如 ``wearing helmet``
   而不是各实例自己的措辞），标签才可比。否定谓词带上 ``not``，直接对上原数据里
   ``not X`` 的根。
3. **输出 JSONL 追加写**，可中断续跑。
4. **两个成本杠杆**：``--no-thinking`` 关掉思维链（判断的 output token 实测均值
   3187，由思维链主导）；``--probe-per-caption`` 先判每个根的一小部分实例，把
   几乎恒定的条件从这个根上剪掉，再判其余实例。探针轮的标签不会被浪费——对存活
   下来的条件，那些标签本来就算数，过滤后保留，于是同一个根内所有实例的问句集合
   保持一致。

    python step5_judge_predicates.py --image-names 0002-fsc147-1386.jpg
    python step5_judge_predicates.py --probe-per-caption 6 --no-thinking
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.io import (
    close_all,
    done_ids,
    get_writer,
    iter_jsonl,
    write_json_atomic,
)
from recsub.llm import OnlineModel, build_cropper, load_three_views
from recsub.prompts import build_instance_messages, build_judgment_prompt
from recsub.utils import match_json

LABELS = ("yes", "no", "unknown")
Sink = Callable[[dict], Awaitable[None]]


def load_caption_predicates(candidates_path: Path) -> tuple[dict, dict, dict]:
    """读 step4 的候选，整理成「根 → 谓词列表 / 问句列表 / 问句→id」。

    同一个规范写法可能对应多个 predicate_id（例如同为 ``white`` 的颜色与材质），
    因此问句按写法去重，标签再展开回所有同写法的 id。
    """
    with candidates_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    by_root: dict[tuple[str, str], list[dict]] = {}
    phrases_by_root: dict[tuple[str, str], tuple[str, ...]] = {}
    ids_by_display: dict[tuple[str, str], dict[str, list[str]]] = {}
    for image_name, captions in payload.items():
        for caption, entry in captions.items():
            key = (str(image_name), str(caption))
            predicates = list(entry.get("predicates") or ())
            by_root[key] = predicates
            display_map: dict[str, list[str]] = defaultdict(list)
            ordered: list[str] = []
            for predicate in predicates:
                display = str(predicate.get("display") or predicate.get("value") or "")
                if not display:
                    continue
                if display not in display_map:
                    ordered.append(display)
                display_map[display].append(str(predicate["predicate_id"]))
            phrases_by_root[key] = tuple(ordered)
            ids_by_display[key] = dict(display_map)
    return by_root, phrases_by_root, ids_by_display


def parse_labels(content: str, phrases: Sequence[str]) -> dict[str, str] | None:
    """解析判断输出；缺项与非法值记为 unknown。``None`` 表示整体解析失败。"""
    parsed = match_json(content)
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("labels")
    if not isinstance(raw, Mapping):
        return None
    return {phrase: (raw.get(phrase) if isinstance(raw.get(phrase), str)
                                        and raw.get(phrase) in LABELS else "unknown")
            for phrase in phrases}


def build_work(instances: Iterable[Mapping[str, Any]], phrases_by_root, completed: set[int],
               image_names: set[str] | None) -> dict[tuple[str, str], list[dict]]:
    """按根收集待判实例；只保留判断需要的字段，控制 28.6 万条时的内存。"""
    work: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for instance in instances:
        instance_id = instance["instance_id"]
        if instance_id in completed:
            continue
        if image_names is not None and instance["image_name"] not in image_names:
            continue
        key = (str(instance["image_name"]), str(instance["caption"]))
        if key not in phrases_by_root:
            continue
        work[key].append({
            "instance_id": instance_id,
            "image_name": instance["image_name"],
            "caption": instance["caption"],
            "category": instance["category"],
            "point": instance["point"],
        })
    return dict(work)


async def judge_one(online: OnlineModel, cropper, item: Mapping[str, Any],
                    phrases: Sequence[str], image_dir: str) -> dict:
    base = {
        "instance_id": item["instance_id"],
        "image_name": item["image_name"],
        "caption": item["caption"],
        "category": item["category"],
    }
    if not phrases:
        return {**base, "labels": {}, "judgment_missing": True}

    try:
        global_image, context_image, fine_image, _ = await asyncio.to_thread(
            load_three_views, item, image_dir, cropper
        )
    except Exception as error:
        return {**base, "judgment_failed": True, "reason": f"裁剪失败: {error}"}

    completion = await online.complete(build_instance_messages(
        global_image, context_image, fine_image,
        build_judgment_prompt(str(item["caption"]), str(item["category"]), list(phrases)),
    ))
    if completion is None:
        return {**base, "judgment_failed": True, "reason": "在线调用失败"}

    labels = parse_labels(completion.content, phrases)
    if labels is None:
        return {**base, "judgment_failed": True, "reason": "输出解析失败",
                **completion.usage(), "attempts": completion.attempts}
    return {**base, "labels": labels, **completion.usage(), "attempts": completion.attempts}


def restrict_labels(record: Mapping[str, Any], keep: Sequence[str]) -> dict:
    """把记录的问句标签过滤到 ``keep`` 集合，供剪枝后复用探针结果。"""
    labels = record.get("labels")
    if not isinstance(labels, Mapping):
        return dict(record)
    return {**record, "labels": {phrase: labels[phrase] for phrase in keep if phrase in labels}}


def expand_labels(record: Mapping[str, Any], ids_by_display: Mapping[str, list[str]]) -> dict:
    """把「问句 → 标签」展开成「predicate_id → 标签」。"""
    labels = record.get("labels")
    if not isinstance(labels, Mapping):
        return dict(record)
    expanded: dict[str, str] = {}
    for display, label in labels.items():
        for predicate_id in ids_by_display.get(display, ()):
            expanded[predicate_id] = label
    return {**record, "labels": expanded}


def ratio_prune(records: Iterable[Mapping[str, Any]], phrases: Sequence[str],
                yes_ratio: float, no_ratio: float, min_instances: int) -> tuple[list[str], list[str]]:
    """从探针标签里挑出「几乎恒定」的问句；返回 (保留, 剪掉)。"""
    tally = {phrase: Counter() for phrase in phrases}
    for record in records:
        labels = record.get("labels")
        if not isinstance(labels, Mapping):
            continue
        for phrase in phrases:
            label = labels.get(phrase)
            if label in LABELS:
                tally[phrase][label] += 1
    kept, pruned = [], []
    for phrase in phrases:
        counted = sum(tally[phrase][label] for label in LABELS)
        if counted < min_instances:
            # 样本太少不能下结论，宁可多花 token 也不能误剪。
            kept.append(phrase)
            continue
        yes_share = tally[phrase]["yes"] / counted
        (pruned if yes_share >= yes_ratio or yes_share <= no_ratio else kept).append(phrase)
    return kept, pruned


async def run(args: argparse.Namespace) -> int:
    by_root, phrases_by_root, ids_by_display = load_caption_predicates(args.candidates)
    with_predicates = sum(1 for phrases in phrases_by_root.values() if phrases)
    print(f"✅ 候选根 {len(by_root)} 个，其中 {with_predicates} 个有候选谓词")

    completed = done_ids(args.output, exclude_flag="judgment_failed")
    if completed:
        print(f"发现已有结果：跳过 {len(completed)} 个已判断实例")

    cropper = build_cropper(iter_jsonl(args.instances))
    image_names = None
    if args.image_names:
        image_names = {name.strip() for name in args.image_names.split(",") if name.strip()}

    work = build_work(iter_jsonl(args.instances), phrases_by_root, completed, image_names)
    total_instances = sum(len(items) for items in work.values())
    if not total_instances:
        print("没有需要判断的实例")
        return 0
    print(f"⏳ 待判断 {total_instances} 个实例 / {len(work)} 个根，批大小 {args.batch_size}")

    online = OnlineModel.create(
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        enable_thinking=args.enable_thinking,
    )
    writer = get_writer(args.output)
    counters = Counter()
    input_tokens = output_tokens = 0

    async def write_record(record: dict) -> None:
        nonlocal input_tokens, output_tokens
        key = (record["image_name"], record["caption"])
        if record.get("judgment_failed"):
            counters["failed"] += 1
        else:
            counters["judged"] += 1
            input_tokens += record.get("input_tokens") or 0
            output_tokens += record.get("output_tokens") or 0
            for label in (record.get("labels") or {}).values():
                counters[label] += 1
        await writer.append(expand_labels(record, ids_by_display.get(key, {})))
        counters["written"] += 1
        if counters["written"] % max(1, args.batch_size) == 0 or counters["written"] == total_instances:
            print(f"  进度 {counters['written']}/{total_instances}  "
                  f"已判 {counters['judged']} 失败 {counters['failed']}")

    async def dispatch(items: Sequence[dict], phrases_for: Mapping, sink: Sink) -> None:
        """按批并发判断一组实例，逐条交给 ``sink``。"""
        batch_size = max(1, args.batch_size)
        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            results = await asyncio.gather(*(
                judge_one(online, cropper, item, phrases_for[(item["image_name"], item["caption"])], args.image_dir)
                for item in batch
            ))
            for record in results:
                await sink(record)

    active_phrases: dict = dict(phrases_by_root)
    constant_pairs: dict[str, list[str]] = {}

    if args.probe_per_caption > 0:
        probe_items = [item for items in work.values() for item in items[:args.probe_per_caption]]
        collected: dict[tuple[str, str], list[dict]] = defaultdict(list)

        async def collect(record: dict) -> None:
            if not record.get("judgment_failed"):
                collected[(record["image_name"], record["caption"])].append(record)

        print(f"⏳ 探针轮：{len(probe_items)} 个实例")
        await dispatch(probe_items, active_phrases, collect)

        pool = Counter()
        for key, phrases in phrases_by_root.items():
            kept, pruned = ratio_prune(
                collected.get(key, ()), phrases,
                args.constant_yes_ratio, args.constant_no_ratio, args.probe_min_instances,
            )
            active_phrases[key] = tuple(kept)
            if pruned:
                constant_pairs[f"{key[0]}\t{key[1]}"] = list(pruned)
                pool.update(pruned)
        print(f"✅ 探针剪掉 {sum(len(v) for v in constant_pairs.values())} 个「根×条件」"
              f"（几乎恒定，无法划分父集）")
        if pool:
            print(f"   被剪最多的条件：{dict(pool.most_common(8))}")

        # 探针轮的标签在存活条件上仍然有效，过滤后保留，不重问。
        for key, records in collected.items():
            survivors = active_phrases.get(key, ())
            for record in records:
                await write_record(restrict_labels(record, survivors))
        # 探针轮失败或没跑到的实例（样本不足时该根仍保留全部条件）也在这个集合里，
        # 下面按全量再跑一遍：已完成集合里没有它们，不会重复计数。
        judged_ids = {record["instance_id"] for records in collected.values() for record in records}
        remaining = [item for items in work.values() for item in items
                     if item["instance_id"] not in judged_ids]
        print(f"⏳ 剩余 {len(remaining)} 个实例按剪枝后的条件判断")
        await dispatch(remaining, active_phrases, write_record)
    else:
        await dispatch([item for items in work.values() for item in items],
                       active_phrases, write_record)

    close_all()

    if constant_pairs:
        write_json_atomic(args.constant_output, {
            "yes_ratio_threshold": args.constant_yes_ratio,
            "no_ratio_threshold": args.constant_no_ratio,
            "probe_per_caption": args.probe_per_caption,
            "pairs": constant_pairs,
        })
        print(f"✅ 恒定条件记录 → {args.constant_output}")

    print(f"\n✅ 判断完成：已判 {counters['judged']}，失败 {counters['failed']}")
    print(f"   标签分布：yes={counters['yes']} no={counters['no']} unknown={counters['unknown']}")
    if counters["failed"]:
        print(f"⚠️ {counters['failed']} 个失败实例带 judgment_failed 且未计入已完成，重跑会自动重试")
    if counters["judged"]:
        print(f"   token：input {input_tokens}，output {output_tokens}，"
              f"平均 output {output_tokens / counters['judged']:.0f}")
    print(f"   结果 → {args.output}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="逐实例闭集三值判断")
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--candidates", type=Path, default=path("predicate_candidates"),
                        help="归一化候选（step4 产出）")
    parser.add_argument("--image-dir", default=CONFIG["image_dir"],
                        help="原图目录")
    parser.add_argument("--output", type=Path, default=path("predicate_labels"))
    parser.add_argument("--constant-output", type=Path, default=Path("anno/predicate_constant.json"),
                        help="探针剪掉的「根×恒定条件」记录")
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--batch-size", type=int, default=CONFIG["llm"]["batch_size"])
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--enable-thinking", dest="enable_thinking", action="store_true",
                        default=CONFIG["llm"]["enable_thinking"])
    parser.add_argument("--no-thinking", dest="enable_thinking", action="store_false",
                        help="关闭思维链；判断的 output token 由思维链主导，值得实测")
    parser.add_argument("--probe-per-caption", type=int, default=0,
                        help="探针轮每根先判这么多实例以剪掉恒定条件；0 表示全量判断")
    parser.add_argument("--probe-min-instances", type=int, default=4,
                        help="探针至少要判这么多实例才敢剪枝，样本不足时宁可多花 token")
    parser.add_argument("--constant-yes-ratio", type=float,
                        default=CONFIG["gates"]["constant_yes_ratio"])
    parser.add_argument("--constant-no-ratio", type=float,
                        default=CONFIG["gates"]["constant_no_ratio"])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    for name in ("instances", "candidates"):
        if not getattr(args, name).is_file():
            print(f"❌ 找不到 {name}：{getattr(args, name)}", file=sys.stderr)
            return 1
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("已中断；结果已按条落盘，重跑会自动续接", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 判断失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
