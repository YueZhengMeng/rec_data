"""step4：把开放提取的候选短语归一化到词表槽位。

1. **开放短语先根内去重，再匹配整张人工审核词表**。每根先做字符串/
   embedding 去重；整个词表预编码一次，词表向量跨根保留，开放短语向量
   每根结束后释放。命中词表的候选按“同类相似 / 跨类相似”标记。
2. **父属性冗余由代码硬过滤**，不问 LLM：跨类复用时也排除与父属性
   同维度、取值、动词和极性的词表谓词。
3. **候选预算分四层**：同类相似、跨类相似、同类不相似、LLM 尾部；LLM 尾部
   默认关闭，需显式传 ``--llm-tail``。新生成谓词单独存入
   ``vocabulary_generated.json``，支持消融。
4. 归一化按 caption 逐个做，但**尾部 LLM 调用按类别批量**。

    python step4_normalize_predicates.py --image-names 0000-fsc147-1106.jpg
    python step4_normalize_predicates.py --no-embedding        # 只跑规则级
    python step4_normalize_predicates.py --llm-tail            # 启用 LLM 尾部
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tqdm import tqdm

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.embedding import Qwen3EmbeddingEncoder
from recsub.instances import enrich_with_instances, load_parents
from recsub.io import iter_jsonl, write_json_atomic
from recsub.llm_api import OnlineModel
from recsub.normalize import (
    CANDIDATE_TIERS,
    DROP_CROSS_CAPTION_STRING,
    CaptionResult,
    Dropped,
    NormalizerSettings,
    PredicateNormalizer,
    accumulator_to_evidence,
    assemble_budgeted_candidates,
    build_vocabulary_lines,
    collect_evidence,
    group_unresolved,
    iter_caption_groups,
    merge_evidence,
    parse_tail_response,
    summarize,
)
from recsub.prompts import build_normalize_tail_messages
from recsub.similarity import compatible
from recsub.utils import extract_and_parse_json
from recsub.vocabulary import Vocabulary, normalize_surface


def check_cross_caption_duplicates(image_name: str, results: Sequence[CaptionResult],
                                   phrases_by_caption: Mapping[str, set[str]],
                                   normalizer: PredicateNormalizer,
                                   vectors: dict, threshold: float) -> None:
    """丢弃与其他 caption 属性字符串相同的候选；语义相似只打印。"""
    printed: set[tuple[str, str, str, str]] = set()
    for result in results:
        retained = []
        for candidate in result.candidates:
            exact_matches: list[tuple[str, str]] = []
            for other_caption, phrases in phrases_by_caption.items():
                if other_caption == result.caption:
                    continue
                seen_phrases: set[str] = set()
                for phrase in sorted(phrases):
                    normalized = normalize_surface(phrase)
                    if normalized in seen_phrases:
                        continue
                    seen_phrases.add(normalized)
                    key = tuple(sorted((result.caption, other_caption))) + (
                        candidate.predicate_id, normalized
                    )
                    if normalize_surface(candidate.display) == normalized:
                        exact_matches.append((other_caption, phrase))
                        print(f"⚠️ 同图属性重复 [字符串·丢弃] {image_name} | {result.caption!r} "
                              f"候选 {candidate.display!r} ({candidate.predicate_id}) "
                              f"↔ 其他 caption {other_caption!r} 的属性 {phrase!r}")
                        continue
                    if key in printed or not compatible(candidate.display, phrase)[0]:
                        continue
                    similarity = normalizer.duplicate_similarity(candidate.display, phrase, vectors)
                    if similarity is None or similarity < threshold:
                        continue
                    printed.add(key)
                    print(f"⚠️ 同图属性重复 [语义] {image_name} | {result.caption!r} "
                          f"候选 {candidate.display!r} ({candidate.predicate_id}) "
                          f"↔ 其他 caption {other_caption!r} 的属性 {phrase!r}，"
                          f"余弦={similarity:.3f}")
            if exact_matches:
                details = "; ".join(f"{caption!r} 的属性 {phrase!r}"
                                    for caption, phrase in exact_matches)
                result.dropped.append(Dropped(candidate.display, DROP_CROSS_CAPTION_STRING,
                                              details, candidate.predicate_id))
            else:
                retained.append(candidate)
        result.candidates = retained


def compact_candidate(candidate, root_category: str) -> dict[str, Any]:
    """省去可由根或默认值推断的字段，仍保留 step5/6 所需数据。"""
    payload = {
        "predicate_id": candidate.predicate_id,
        "display": candidate.display,
        "dimension": candidate.dimension,
        "value": candidate.value,
    }
    if candidate.category != root_category:
        payload["category"] = candidate.category
    if candidate.verb:
        payload["verb"] = candidate.verb
    if candidate.polarity != "positive":
        payload["polarity"] = candidate.polarity
    if candidate.source != "reused":
        payload["source"] = candidate.source
    return payload


def compact_root(result: CaptionResult) -> dict[str, Any]:
    """四类分组直接作为输出；父谓词 ID 供 step6 二次冗余检查。"""
    groups = {tier: [] for tier in CANDIDATE_TIERS}
    for candidate in result.candidates:
        groups[candidate.selection_tier].append(compact_candidate(candidate, result.category))
    return {"category": result.category,
            "parent_predicate_id": result.parent_predicate_id,
            "candidates": groups,
            "dropped": [{"predicate_id": item.predicate_id, "phrase": item.phrase,
                         "reason": item.reason, "detail": item.detail}
                        for item in result.dropped if item.reason == DROP_CROSS_CAPTION_STRING]}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候选短语归一化到词表槽位")
    parser.add_argument("--input", type=Path, default=path("extract_result_online"),
                        help="开放提取结果（本地入口用 --input anno/extract_result_local.jsonl）")
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
    parser.add_argument("--max-candidates-per-root", type=int,
                        default=CONFIG["normalize"]["max_candidates_per_root"],
                        help="每根发给 step5 的谓词总预算；0 表示不发送")
    parser.add_argument("--embedding", action=argparse.BooleanOptionalAction,
                        default=CONFIG["normalize"]["use_embedding"],
                        help="是否做向量级匹配；--no-embedding 只跑规则级")
    parser.add_argument("--llm-tail", action=argparse.BooleanOptionalAction,
                        default=CONFIG["normalize"]["use_llm_tail"],
                        help="是否用 LLM 尾部处理未匹配短语；"
                             "--no-llm-tail 时未解析短语直接记为 unresolved")
    parser.add_argument("--dedup-similarity-threshold", type=float,
                        default=CONFIG["normalize"]["dedup_similarity_threshold"],
                        help="根内开放短语两两合并的余弦阈值")
    parser.add_argument("--similarity-threshold", type=float,
                        default=CONFIG["normalize"]["similarity_threshold"],
                        help="开放短语匹配闭集词表的余弦阈值")
    parser.add_argument("--similarity-margin", type=float, default=CONFIG["normalize"]["similarity_margin"])
    parser.add_argument("--cross-caption-duplicate-threshold", type=float,
                        default=CONFIG["normalize"]["cross_caption_duplicate_threshold"],
                        help="语义重复仅提示的阈值；字符串重复始终丢弃并记录 dropped")
    parser.add_argument("--allow-refinement-split", action=argparse.BooleanOptionalAction,
                        default=CONFIG["normalize"]["allow_refinement_split"],
                        help="允许把「白色鞋带」这类细化短语留在独立槽位，"
                             "而不是并进父属性级别")
    parser.add_argument("--max-new-predicates-per-category", type=int,
                        default=CONFIG["normalize"]["max_new_predicates_per_category"],
                        help="每类接受的 LLM 新生成谓词上限")
    parser.add_argument("--tail-batch-size", type=int,
                        default=CONFIG["normalize"]["llm_tail_batch_size"],
                        help="每次尾部请求带多少条待解析短语")
    parser.add_argument("--concurrency", type=int,
                        default=CONFIG["normalize"]["llm_tail_concurrency"],
                        help="尾部请求的并发数")
    return parser.parse_args(argv)


def build_settings(args: argparse.Namespace) -> NormalizerSettings:
    return NormalizerSettings(
        dedup_similarity_threshold=args.dedup_similarity_threshold,
        similarity_threshold=args.similarity_threshold,
        similarity_margin=args.similarity_margin,
        allow_refinement_split=args.allow_refinement_split,
        max_new_predicates_per_category=args.max_new_predicates_per_category,
    )


async def resolve_tails(online: OnlineModel, vocabulary: Vocabulary,
                        settings: NormalizerSettings, batch_size: int,
                        grouped: Mapping[str, list]) -> tuple[dict[str, dict], list, list[str]]:
    """按类别批量调用 LLM 解决剩余短语，返回 (逐短语决策, 新谓词, 警告)。"""
    decisions: dict[tuple[str, str], dict] = {}
    warnings: list[str] = []
    new_entries: dict[str, Any] = {}
    existing_ids = vocabulary.predicate_ids()

    for category in sorted(grouped):
        items = grouped[category]
        lines = build_vocabulary_lines(vocabulary, category)
        batches = [items[start:start + batch_size] for start in range(0, len(items), batch_size)]
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
            warnings.extend(batch_warnings)
            accepted: set[str] = set()
            for entry in batch_new:
                previous = new_entries.get(entry.predicate_id)
                if previous is not None and previous.slot.key() != entry.slot.key():
                    warnings.append(f"{category}: 生成谓词 ID 冲突 {entry.predicate_id}")
                    continue
                if (previous is None
                        and sum(item.category == category for item in new_entries.values())
                        >= settings.max_new_predicates_per_category):
                    warnings.append(f"{category}: 生成谓词超出类别上限 {entry.predicate_id}")
                    continue
                new_entries.setdefault(entry.predicate_id, entry)
                accepted.add(entry.predicate_id)
            for phrase, decision in batch_decisions.items():
                if decision.get("kind") == "new" and decision.get("predicate_id") not in accepted:
                    continue
                decisions[(category, phrase)] = decision

    return decisions, list(new_entries.values()), warnings


async def run(args: argparse.Namespace) -> int:
    vocabulary = Vocabulary.load(args.vocabulary)
    settings = build_settings(args)
    print(f"✅ 词表 {len(vocabulary)} 条；开放去重阈值 {settings.dedup_similarity_threshold}，"
          f"闭集匹配阈值 {settings.similarity_threshold}，margin {settings.similarity_margin}，"
          f"细化不合并 {settings.allow_refinement_split}")

    parents = load_parents(args.instances)
    image_names = None
    if args.image_names:
        image_names = {name.strip() for name in args.image_names.split(",") if name.strip()}
    print(f"✅ 父 caption 索引：{len(parents)} 个根")

    encoder = None
    if args.embedding:
        encoder = Qwen3EmbeddingEncoder(
            CONFIG["normalize"]["embedding_model"],
            batch_size=CONFIG["normalize"]["embedding_batch_size"],
            device=CONFIG["normalize"]["embedding_device"],
        )
        print(f"✅ 向量模型：{CONFIG['normalize']['embedding_model']}")

    normalizer = PredicateNormalizer(vocabulary, encoder=encoder, settings=settings)
    if encoder is not None:
        normalizer.prepare_vocabulary_vectors()
        print(f"✅ 全词表预编码 {normalizer.vocabulary_vector_count} 个不同写法")

    # 只带 instance_id 的提取记录靠与实例表归并配对补上图片信息。
    enriched = enrich_with_instances(iter_jsonl(args.input), args.instances)
    records = (
        record for record in enriched
        if image_names is None or str(record.get("image_name")) in image_names
    )

    # 在线提取完成顺序不保证按图连续，先按根合并证据，再按图编码。
    accumulators: dict[tuple[str, str], dict[str, list]] = {}
    skipped = 0
    for key, category, group in iter_caption_groups(records):
        parent_info = parents.get(key)
        if parent_info is None:
            skipped += 1
            continue
        if category != parent_info[0]:
            raise ValueError(f"提取结果与父根的类别不一致：{key} ({category} / {parent_info[0]})")
        merge_evidence(accumulators.setdefault(key, {}), collect_evidence(group))

    keys_by_image: dict[str, list[tuple[str, str]]] = {}
    for key in sorted(accumulators):
        keys_by_image.setdefault(key[0], []).append(key)
    results: list[CaptionResult] = []
    payload: dict[str, dict[str, Any]] = {}
    unresolved_count = 0
    for image_name, image_keys in tqdm(keys_by_image.items(), total=len(keys_by_image),
                                       desc="处理图片"):
        evidence_by_key = {key: accumulator_to_evidence(accumulators.pop(key))
                           for key in image_keys}
        # 原标注属性 + 开放提取短语，用于本图片最终候选的只读重复提示。
        phrases_by_caption: dict[str, set[str]] = {}
        for key, evidence in evidence_by_key.items():
            phrases_by_caption[key[1]] = ({parents[key][1]} if parents[key][1] else set()) | {
                item.phrase for item in evidence
            }
        vectors: dict = {}
        try:
            # 同图开放短语和跨 caption 比对所需的原标注属性一次批量编码。
            phrases = {item.phrase for evidence in evidence_by_key.values() for item in evidence}
            if len(evidence_by_key) > 1:
                phrases.update(parents[key][1] for key in evidence_by_key)
            normalizer.encode_image_phrases(phrases, vectors)
            image_results: list[CaptionResult] = []
            for key in sorted(evidence_by_key):
                parent_category, parent_attribute, parent_type = parents[key]
                parent = vocabulary.parent_slot(parent_category, parent_type, parent_attribute)
                result = normalizer.normalize_caption(
                    key[0], key[1], parent_category, parent, evidence_by_key[key], vectors=vectors
                )
                if args.llm_tail:
                    results.append(result)
                else:
                    unresolved_count += len(result.unresolved)
                image_results.append(result)
            if not args.llm_tail:
                for result in image_results:
                    parent_info = parents[(result.image_name, result.caption)]
                    parent = vocabulary.parent_slot(parent_info[0], parent_info[2], parent_info[1])
                    assemble_budgeted_candidates(result, vocabulary, parent,
                                                 args.max_candidates_per_root)
                check_cross_caption_duplicates(image_name, image_results, phrases_by_caption,
                                               normalizer, vectors,
                                               args.cross_caption_duplicate_threshold)
                for result in image_results:
                    payload.setdefault(image_name, {})[result.caption] = compact_root(result)
        finally:
            normalizer.release_image_vectors(vectors)
    if skipped:
        print(f"⚠️ 跳过 {skipped} 个在实例表里找不到父 caption 的组")

    processed_count = sum(len(captions) for captions in payload.values()) if not args.llm_tail else len(results)
    print(f"✅ 已归一化 {processed_count} 个 caption（规则级"
          f"{' + 向量级' if encoder else ''}）")
    if encoder is not None:
        print(f"   常驻词表向量 {normalizer.vocabulary_vector_count} 条；开放短语临时向量已释放")

    # ---- LLM 尾部 ----
    new_entries: list = []
    warnings: list[str] = []
    use_tail = args.llm_tail
    grouped = group_unresolved(results) if use_tail else {}
    unresolved_total = sum(len(items) for items in grouped.values()) if use_tail else unresolved_count
    if unresolved_total and use_tail:
        print(f"⏳ 未解析短语 {unresolved_total} 个，交给 LLM 尾部")
        online = OnlineModel.create(concurrency=args.concurrency)
        decisions, new_entries, warnings = await resolve_tails(
            online, vocabulary, settings, args.tail_batch_size, grouped
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
        print(f"⚠️ 未解析短语 {unresolved_total} 个（默认关闭 LLM 尾部；需要时传 --llm-tail）")

    if use_tail:
        # LLM 尾部决策完成后才知道最终候选；按图片重建临时向量用于提示。
        image_results: dict[str, list[CaptionResult]] = {}
        for result in results:
            image_results.setdefault(result.image_name, []).append(result)
        for image_name, same_image in image_results.items():
            phrases_by_caption = {}
            for result in same_image:
                parent_attribute = parents[(image_name, result.caption)][1]
                phrases_by_caption[result.caption] = ({parent_attribute} if parent_attribute else set()) | {
                    phrase for group in result.open_phrase_groups for phrase in group["raw_phrases"]
                }
            vectors: dict = {}
            try:
                normalizer.encode_image_phrases(
                    (phrase for phrases in phrases_by_caption.values() for phrase in phrases),
                    vectors,
                )
                for result in same_image:
                    parent_info = parents[(image_name, result.caption)]
                    parent = vocabulary.parent_slot(parent_info[0], parent_info[2], parent_info[1])
                    assemble_budgeted_candidates(result, vocabulary, parent,
                                                 args.max_candidates_per_root)
                check_cross_caption_duplicates(image_name, same_image, phrases_by_caption,
                                               normalizer, vectors,
                                               args.cross_caption_duplicate_threshold)
            finally:
                normalizer.release_image_vectors(vectors)

    # ---- 紧凑输出 ----
    selected_new_ids: set[str] = set()
    if use_tail:
        for result in results:
            selected_new_ids.update(candidate.predicate_id for candidate in result.candidates
                                    if candidate.source == "generated")
            payload.setdefault(result.image_name, {})[result.caption] = compact_root(result)
    write_json_atomic(args.output, payload, indent=4)

    selected_new = [entry for entry in new_entries if entry.predicate_id in selected_new_ids]
    Vocabulary(entries=selected_new, metadata={
        "origin": "generated-by-step4-tail", "source_extract": str(args.input),
        "parent_vocabulary": str(args.vocabulary), "count": len(selected_new),
    }).save(args.generated_vocabulary)
    print(f"✅ 入选生成谓词 {len(selected_new)} 个 → {args.generated_vocabulary}")

    if use_tail:
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
    if args.max_candidates_per_root < 0:
        print("❌ --max-candidates-per-root 必须 ≥0", file=sys.stderr)
        return 1
    if len({args.input.resolve(), args.output.resolve(),
            args.generated_vocabulary.resolve()}) != 3:
        print("❌ 输入、候选输出与生成词表路径必须不同", file=sys.stderr)
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
