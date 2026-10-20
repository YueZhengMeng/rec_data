"""step5 在线入口：逐实例闭集三值判断。

1. **失败与「看不见」分开**。调用失败/解析失败写成 ``judgment_failed`` 记录，
   既不计入 unknown 也不计入已完成集合，重跑自动重试。
2. **用规范写法发问**。同一个谓词在所有实例上问法一致（如 ``wearing helmet``
   而不是各实例自己的措辞），标签才可比。否定谓词带上 ``not``。
3. **输出 JSONL 追加写**，可中断续跑；运行指纹写在旁挂标记文件里。
4. **两个成本杠杆**：``--no-enable-thinking`` 关掉思维链（判断的 output token 由
   思维链主导，实测均值 3187）；``--probe-per-caption`` 先判每个根的一小部分实例，把
   几乎恒定的条件从这个根上剪掉，再判其余实例。探针轮的标签不会被浪费——对存活
   下来的条件，那些标签本来就算数，过滤后保留，于是同一个根内所有实例的问句集合
   保持一致。

    python step5_judge_predicates_online.py --image-names 0002-fsc147-1386.jpg
    python step5_judge_predicates_online.py --probe-per-caption 6 --no-enable-thinking
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from recsub.config import CONFIG, crop_config, online_sampling, path
from recsub.console import configure_console
from recsub.extract import file_digest, run_fingerprint
from recsub.io import (
    close_all,
    get_writer,
    iter_jsonl,
    write_json_atomic,
)
from recsub.judgment import (
    build_work, completed_judgments, expand_labels, load_caption_predicates,
    parse_labels, ratio_prune, restrict_labels,
)
from recsub.llm_api import OnlineModel, build_cropper, load_three_views
from recsub.online_batch import run_online_batch
from recsub.prompts import PREDICATE_JUDGMENT_PROMPT, build_instance_messages, build_judgment_prompt

# 仅在 step5 覆盖 Qwen 的 presence_penalty，允许候选短语原样重复；DeepSeek 不发送。
PRESENCE_PENALTY = 0.0
Sink = Callable[[dict], Awaitable[None]]


def judgment_fingerprint(args: argparse.Namespace) -> str:
    """绑定候选顺序、裁剪与判断参数；更换其中任一项后必须用新的输出文件。"""
    return run_fingerprint({
        "backend": "online", "provider": CONFIG["provider"],
        "candidates_sha256": file_digest(args.candidates),
        "instances_sha256": file_digest(args.instances),
        "image_dir": str(Path(args.image_dir).resolve()),
        "model": CONFIG["model_name"], "base_url": CONFIG["base_url"],
        "enable_thinking": args.enable_thinking,
        "sampling": online_sampling(
            enable_thinking=args.enable_thinking, presence_penalty=PRESENCE_PENALTY,
        ),
        "probe_per_caption": args.probe_per_caption,
        "probe_min_instances": args.probe_min_instances,
        "constant_yes_ratio": args.constant_yes_ratio,
        "constant_no_ratio": args.constant_no_ratio,
        "crop": asdict(crop_config()),
        "prompt": PREDICATE_JUDGMENT_PROMPT,
    })


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
        build_judgment_prompt(str(item.get("attributes") or ""), str(item["category"]), list(phrases)),
    ))
    if completion is None:
        return {**base, "judgment_failed": True, "reason": "在线调用失败"}

    labels = parse_labels(completion.content, phrases)
    if labels is None:
        return {**base, "judgment_failed": True, "reason": "输出解析失败",
                **completion.usage()}
    return {**base, "labels": labels, **completion.usage()}


async def run(args: argparse.Namespace) -> int:
    if not args.check_fingerprint:
        print(
            "警告：已关闭运行指纹校验（--no-check-fingerprint），续跑结果不会校验配置一致性。"
        )
    by_root, phrases_by_root, ids_by_display = load_caption_predicates(args.candidates)
    with_predicates = sum(1 for phrases in phrases_by_root.values() if phrases)
    print(f"✅ 候选根 {len(by_root)} 个，其中 {with_predicates} 个有候选谓词")

    fingerprint = judgment_fingerprint(args)
    completed, prior_rows = completed_judgments(
        args.output, fingerprint, check_fingerprint=args.check_fingerprint,
    )
    if prior_rows and args.probe_per_caption > 0:
        raise ValueError("探针模式在部分写入后不能安全重算剪枝；请指定新的 --output")
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
        presence_penalty=PRESENCE_PENALTY,
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

    async def dispatch(items: Sequence[dict], phrases_for: Mapping, sink: Sink,
                       *, phase: str = "判断") -> None:
        """按批并发判断，结果返回后立即交给 sink，不等待其他请求。"""
        batch_size = max(1, args.batch_size)
        total_batches = (len(items) + batch_size - 1) // batch_size

        async def handle(item: Mapping[str, Any]) -> None:
            record = await judge_one(
                online, cropper, item, phrases_for[(item["image_name"], item["caption"])],
                args.image_dir,
            )
            await sink(record)

        for batch_number, start in enumerate(range(0, len(items), batch_size), 1):
            batch = items[start:start + batch_size]
            await run_online_batch(batch, handle,
                                   desc=f"step5 {phase} batch {batch_number}/{total_batches}")

    active_phrases: dict = dict(phrases_by_root)
    constant_pairs: dict[str, list[str]] = {}

    try:
        if args.probe_per_caption > 0:
            probe_items = [item for items in work.values() for item in items[:args.probe_per_caption]]
            collected: dict[tuple[str, str], list[dict]] = defaultdict(list)

            async def collect(record: dict) -> None:
                if not record.get("judgment_failed"):
                    collected[(record["image_name"], record["caption"])].append(record)

            print(f"⏳ 探针轮：{len(probe_items)} 个实例")
            await dispatch(probe_items, active_phrases, collect, phase="探针")

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
            # 探针失败的实例仍在 remaining 中，按剪枝后的条件重试。
            judged_ids = {record["instance_id"] for records in collected.values() for record in records}
            remaining = [item for items in work.values() for item in items
                         if item["instance_id"] not in judged_ids]
            print(f"⏳ 剩余 {len(remaining)} 个实例按剪枝后的条件判断")
            await dispatch(remaining, active_phrases, write_record)
        else:
            await dispatch([item for items in work.values() for item in items],
                           active_phrases, write_record)
    finally:
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
    parser.add_argument("--candidates", type=Path, default=path("predicate_candidates_online"),
                        help="归一化候选（step4 产出）")
    parser.add_argument("--image-dir", default=CONFIG["image_dir"],
                        help="原图目录")
    parser.add_argument("--output", type=Path, default=path("predicate_labels_online"))
    parser.add_argument("--check-fingerprint", action=argparse.BooleanOptionalAction,
                        default=CONFIG["resume"]["check_fingerprint"],
                        help="校验输出旁挂的运行指纹；关闭后不会读取既有标记")
    parser.add_argument("--constant-output", type=Path, default=path("predicate_constant_online"),
                        help="探针剪掉的「根×恒定条件」记录")
    parser.add_argument("--image-names", default=None)
    parser.add_argument("--batch-size", type=int, default=CONFIG["online"]["batch_size"])
    parser.add_argument("--concurrency", type=int, default=CONFIG["online"]["concurrency"])
    parser.add_argument("--max-retries", type=int, default=CONFIG["online"]["max_retries"])
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction,
                        default=CONFIG["online"]["enable_thinking"],
                        help="是否启用思维链；判断的 output token 由思维链主导，"
                             "--no-enable-thinking 值得实测")
    parser.add_argument("--probe-per-caption", type=int,
                        default=CONFIG["judge"]["probe_per_caption"],
                        help="探针轮每根先判这么多实例以剪掉恒定条件；0 表示全量判断")
    parser.add_argument("--probe-min-instances", type=int,
                        default=CONFIG["judge"]["probe_min_instances"],
                        help="探针至少要判这么多实例才敢剪枝，样本不足时宁可多花 token")
    parser.add_argument("--constant-yes-ratio", type=float,
                        default=CONFIG["judge"]["constant_yes_ratio"])
    parser.add_argument("--constant-no-ratio", type=float,
                        default=CONFIG["judge"]["constant_no_ratio"])
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
