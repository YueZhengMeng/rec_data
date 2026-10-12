"""step4：把开放提取的候选短语归一化到词表槽位（原 step3）。

与原版的关键差别：

1. **合并目标是标注词表，不是「每个 caption 各挑一次」**。原实现先用「≤5 个
   空格分词」过滤，再做 embedding 去重，最后让文本 LLM 从原词里勾选——LLM
   看不到图片、父 caption，也不许改写，无法判断一个词是不是场景全局属性。
   长度门槛还会丢掉关系信息：实测 relation 类有 12.96 万条超过 5 词（占 38%），
   而「短」不等于「通用」（``below the 9 key`` 很短却不可复用）。
2. **父属性冗余由代码硬过滤**，不问 LLM：归一化结果等于父槽位就丢弃。
3. **生成的新谓词单独存一份** ``vocabulary_generated.json``，直接支撑
   「仅原词表 vs 加生成谓词」的消融。
4. 归一化按 caption 逐个做，但**尾部 LLM 调用按类别批量**，把 1.7 万个 caption
   的请求压到每个类别几个批次。

    python step4_normalize_predicates.py --image-names 0000-fsc147-1106.jpg
    python step4_normalize_predicates.py --no-embedding        # 只跑规则级
    python step4_normalize_predicates.py --no-llm-tail         # 不用 LLM 尾部
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.embedding import Qwen3EmbeddingEncoder
from recsub.instances import enrich_with_instances, load_parents
from recsub.io import iter_jsonl, write_json_atomic
from recsub.llm import OnlineModel
from recsub.normalize import (
    CaptionResult,
    NormalizerSettings,
    PredicateNormalizer,
    accumulator_to_evidence,
    build_vocabulary_lines,
    collect_evidence,
    group_unresolved,
    iter_caption_groups,
    merge_evidence,
    parse_tail_response,
    summarize,
)
from recsub.prompts import build_normalize_tail_messages
from recsub.utils import extract_and_parse_json
from recsub.vocabulary import Vocabulary

TAIL_BATCH = 40


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候选短语归一化到词表槽位")
    parser.add_argument("--input", type=Path, default=path("extract_result"),
                        help="开放提取结果（step3 产出）")
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="实例表，用于取父 caption 的类别与原属性")
    parser.add_argument("--vocabulary", type=Path, default=path("vocabulary"),
                        help="谓词词表（step2 产出）")
    parser.add_argument("--output", type=Path, default=path("predicate_candidates"),
                        help="归一化候选输出（图片 → caption → 候选谓词）")
    parser.add_argument("--generated-vocabulary", type=Path,
                        default=path("generated_vocabulary"),
                        help="LLM 尾部新生成的谓词，单独存一份用于消融")
    parser.add_argument("--image-names", default=None,
                        help="只处理这些图片（逗号分隔）")
    parser.add_argument("--no-embedding", dest="use_embedding", action="store_false",
                        default=True, help="跳过向量级匹配，只跑规则级")
    parser.add_argument("--no-llm-tail", dest="use_llm_tail", action="store_false",
                        default=None, help="不做 LLM 尾部，未解析短语直接记为 unresolved")
    parser.add_argument("--similarity-threshold", type=float, default=None)
    parser.add_argument("--similarity-margin", type=float, default=None)
    parser.add_argument("--allow-refinement-merge", dest="allow_refinement_split",
                        action="store_false", default=None,
                        help="允许把「白色鞋带」这类细化短语合并进父属性级别的槽位")
    parser.add_argument("--concurrency", type=int, default=None)
    return parser.parse_args(argv)


def build_settings(args: argparse.Namespace) -> NormalizerSettings:
    defaults = NormalizerSettings(
        similarity_threshold=CONFIG["normalize"]["similarity_threshold"],
    )
    return NormalizerSettings(
        similarity_threshold=(args.similarity_threshold
                              if args.similarity_threshold is not None
                              else defaults.similarity_threshold),
        similarity_margin=(args.similarity_margin
                           if args.similarity_margin is not None
                           else defaults.similarity_margin),
        allow_refinement_split=(defaults.allow_refinement_split
                                if args.allow_refinement_split is None
                                else args.allow_refinement_split),
    )


async def resolve_tails(online: OnlineModel, vocabulary: Vocabulary,
                        settings: NormalizerSettings,
                        grouped: Mapping[str, list]) -> tuple[dict[str, dict], list, list[str]]:
    """按类别批量调用 LLM 解决剩余短语，返回 (逐短语决策, 新谓词, 警告)。"""
    decisions: dict[tuple[str, str], dict] = {}
    warnings: list[str] = []
    new_entries: dict[str, Any] = {}
    existing_ids = vocabulary.predicate_ids()

    for category in sorted(grouped):
        items = grouped[category]
        lines = build_vocabulary_lines(vocabulary, category)
        batches = [items[start:start + TAIL_BATCH] for start in range(0, len(items), TAIL_BATCH)]
        print(f"   尾部 {category}：{len(items)} 个短语 / {len(batches)} 批")

        async def one(batch):
            messages = build_normalize_tail_messages(
                category, lines, [item.phrase for item in batch]
            )
            completion = await online.complete(messages)
            if completion is None:
                return None
            payload = extract_and_parse_json(completion.content)
            return parse_tail_response(payload, vocabulary, category, batch,
                                       settings, existing_ids)

        for batch, outcome in zip(
                batches, await asyncio.gather(*(one(batch) for batch in batches))):
            if outcome is None:
                warnings.append(f"{category}: 一批 {len(batch)} 个短语的尾部调用失败")
                continue
            batch_decisions, batch_new, batch_warnings = outcome
            decisions.update({(category, phrase): decision
                              for phrase, decision in batch_decisions.items()})
            warnings.extend(batch_warnings)
            for entry in batch_new:
                new_entries.setdefault(entry.predicate_id, entry)

    return decisions, list(new_entries.values()), warnings


async def run(args: argparse.Namespace) -> int:
    vocabulary = Vocabulary.load(args.vocabulary)
    settings = build_settings(args)
    print(f"✅ 词表 {len(vocabulary)} 条；相似度阈值 {settings.similarity_threshold}，"
          f"margin {settings.similarity_margin}，细化不合并 {settings.allow_refinement_split}")

    parents = load_parents(args.instances)
    image_names = None
    if args.image_names:
        image_names = {name.strip() for name in args.image_names.split(",") if name.strip()}
    print(f"✅ 父 caption 索引：{len(parents)} 个根")

    encoder = None
    if args.use_embedding:
        encoder = Qwen3EmbeddingEncoder(
            CONFIG["normalize"]["embedding_model"],
            batch_size=CONFIG["normalize"]["embedding_batch_size"],
            device=CONFIG["normalize"]["embedding_device"],
        )
        print(f"✅ 向量模型：{CONFIG['normalize']['embedding_model']}")

    normalizer = PredicateNormalizer(vocabulary, encoder=encoder, settings=settings)

    # 旧格式的提取结果只有 instance_id，靠与实例表归并配对补上图片信息。
    enriched = enrich_with_instances(iter_jsonl(args.input), args.instances)
    records = (
        record for record in enriched
        if image_names is None or str(record.get("image_name")) in image_names
    )

    results_by_key: dict[tuple[str, str], CaptionResult] = {}
    accumulators: dict[tuple[str, str], dict[str, list]] = {}
    skipped = 0
    for key, category, group in iter_caption_groups(records):
        parent_info = parents.get(key)
        if parent_info is None:
            skipped += 1
            continue
        parent_category, parent_attribute, parent_type = parent_info
        parent = vocabulary.parent_slot(parent_category, parent_type, parent_attribute)
        accumulator = accumulators.setdefault(key, {})
        merge_evidence(accumulator, collect_evidence(group))
        results_by_key[key] = normalizer.normalize_caption(
            key[0], key[1], parent_category, parent, accumulator_to_evidence(accumulator)
        )
    results = list(results_by_key.values())
    if skipped:
        print(f"⚠️ 跳过 {skipped} 个在实例表里找不到父 caption 的组")

    print(f"✅ 已归一化 {len(results)} 个 caption（规则级"
          f"{' + 向量级' if encoder else ''}）")
    if encoder is not None:
        print(f"   向量缓存 {encoder.cache_size} 条")

    # ---- LLM 尾部 ----
    new_entries: list = []
    warnings: list[str] = []
    use_tail = args.use_llm_tail
    if use_tail is None:
        use_tail = bool(CONFIG["normalize"]["use_llm_tail"])
    grouped = group_unresolved(results)
    unresolved_total = sum(len(items) for items in grouped.values())
    if unresolved_total and use_tail:
        print(f"⏳ 未解析短语 {unresolved_total} 个，交给 LLM 尾部")
        online = OnlineModel.create(concurrency=args.concurrency)
        decisions, new_entries, warnings = await resolve_tails(
            online, vocabulary, settings, grouped
        )
        for result in results:
            parent_info = parents[(result.image_name, result.caption)]
            parent = vocabulary.parent_slot(parent_info[0], parent_info[2], parent_info[1])
            normalizer.apply_tail(
                result, parent, decisions,
                {entry.predicate_id: entry for entry in new_entries},
            )
        print(f"✅ 尾部：{len(decisions)} 个短语有决策，新增谓词 {len(new_entries)} 个")
    elif unresolved_total:
        print(f"⚠️ 未解析短语 {unresolved_total} 个（已用 --no-llm-tail 关闭尾部）")

    # ---- 输出 ----
    payload: dict[str, dict[str, Any]] = {}
    for result in results:
        payload.setdefault(result.image_name, {})[result.caption] = {
            "category": result.category,
            "parent_predicate_id": result.parent_predicate_id,
            "parent_display": result.parent_display,
            "predicates": [candidate.to_dict() for candidate in result.candidates],
            "dropped": [{"phrase": item.phrase, "reason": item.reason, "detail": item.detail}
                        for item in result.dropped],
        }
    write_json_atomic(args.output, payload)

    if new_entries:
        generated = Vocabulary(entries=new_entries, metadata={
            "origin": "generated-by-step4-tail",
            "source_extract": str(args.input),
            "parent_vocabulary": str(args.vocabulary),
            "count": len(new_entries),
        })
        generated.save(args.generated_vocabulary)
        print(f"✅ 生成谓词 {len(new_entries)} 个 → {args.generated_vocabulary}")

    stats = summarize(results)
    print("\n合并方式：", stats["merge_methods"])
    print("来源：", stats["sources"])
    print("丢弃理由：", stats["drop_reasons"])
    print(f"出现过的不同谓词：{stats['distinct_predicates']}")
    if warnings:
        print(f"\n⚠️ 尾部警告 {len(warnings)} 条（前 10 条）：")
        for warning in warnings[:10]:
            print("   ", warning)
    print(f"\n结果 → {args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    for name in ("input", "instances", "vocabulary"):
        if not getattr(args, name).is_file():
            print(f"❌ 找不到 {name}：{getattr(args, name)}", file=sys.stderr)
            return 1
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 归一化失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
