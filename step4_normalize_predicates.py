"""step4：把开放提取的候选短语归一化到词表槽位。

1. **开放短语先根内去重，再匹配根所属划分的人工审核词表**。每根先做字符串/
   embedding 去重；train 使用训练词表，val/test 使用全量词表，各自预编码并跨根保留，开放短语
   向量每图结束后释放。命中词表的候选按“同类相似 / 跨类相似”标记。
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
from recsub.io import iter_jsonl, load_json_any, write_jsonl_atomic
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
from recsub.vocabulary import Vocabulary, load_split_keys, normalize_surface


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
    parser.add_argument("--vocabulary", type=Path, default=path("vocabulary_train"),
                        help="训练集谓词词表（step2 --split train 产出）")
    parser.add_argument("--vocabulary-all", type=Path, default=path("vocabulary_all"),
                        help="val/test 共用的全量谓词词表（step2 --split all 产出）")
    parser.add_argument("--splits", type=Path, default=path("splits"),
                        help="数据划分 splits.json，按 (图片名, caption) 路由词表")
    parser.add_argument("--output", type=Path, default=path("predicate_candidates_online"),
                        help="归一化候选输出（JSONL，一行一个 caption 的候选谓词）")
    parser.add_argument("--generated-vocabulary", type=Path,
                        default=path("generated_vocabulary_online"),
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


def load_vocabulary_scopes(splits_path: Path) -> dict[tuple[str, str], str]:
    """将 splits.json 的根映射到词表作用域；val/test 共用 all。"""
    splits = load_json_any(splits_path)
    if not isinstance(splits, Mapping):
        raise ValueError(f"{splits_path} 顶层必须是划分对象")
    scopes: dict[tuple[str, str], str] = {}
    for split, scope in (("train", "train"), ("val", "all"), ("test", "all")):
        keys = load_split_keys(splits, split)
        assert keys is not None
        for key in keys:
            previous = scopes.get(key)
            if previous is not None:
                raise ValueError(f"根 {key!r} 在多个数据划分中重复")
            scopes[key] = scope
    return scopes


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
    split_scope_by_key = load_vocabulary_scopes(args.splits)
    vocabularies = {
        "train": Vocabulary.load(args.vocabulary),
        "all": Vocabulary.load(args.vocabulary_all),
    }
    settings = build_settings(args)
    for scope, vocabulary in vocabularies.items():
        print(f"✅ {scope} 词表 {len(vocabulary)} 条："
              f"{args.vocabulary if scope == 'train' else args.vocabulary_all}")
    print(f"✅ 开放去重阈值 {settings.dedup_similarity_threshold}，"
          f"闭集匹配阈值 {settings.similarity_threshold}，margin {settings.similarity_margin}，"
          f"细化不合并 {settings.allow_refinement_split}")

    parents = load_parents(args.instances)
    image_names = None
    if args.image_names:
        image_names = {name.strip() for name in args.image_names.split(",") if name.strip()}
    print(f"✅ 父 caption 索引：{len(parents)} 个根")

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
        if key not in split_scope_by_key:
            raise ValueError(f"提取结果中的根不在 splits.json：{key!r}")
        if category != parent_info[0]:
            raise ValueError(f"提取结果与父根的类别不一致：{key} ({category} / {parent_info[0]})")
        merge_evidence(accumulators.setdefault(key, {}), collect_evidence(group))

    encoder = None
    if args.embedding:
        encoder = Qwen3EmbeddingEncoder(
            CONFIG["normalize"]["embedding_model"],
            batch_size=CONFIG["normalize"]["embedding_batch_size"],
            device=CONFIG["normalize"]["embedding_device"],
        )
        print(f"✅ 向量模型：{CONFIG['normalize']['embedding_model']}")

    normalizers = {
        scope: PredicateNormalizer(vocabulary, encoder=encoder, settings=settings)
        for scope, vocabulary in vocabularies.items()
    }
    if encoder is not None:
        for scope, normalizer in normalizers.items():
            normalizer.prepare_vocabulary_vectors()
            print(f"✅ {scope} 词表预编码 {normalizer.vocabulary_vector_count} 个不同写法")

    keys_by_image: dict[str, list[tuple[str, str]]] = {}
    for key in sorted(accumulators):
        keys_by_image.setdefault(key[0], []).append(key)
    train_root_count = sum(split_scope_by_key[key] == "train" for key in accumulators)
    all_root_count = len(accumulators) - train_root_count
    results: list[CaptionResult] = []
    payload: dict[str, dict[str, Any]] = {}
    unresolved_by_scope = {"train": 0, "all": 0}
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
        vectors_by_scope: dict[str, dict] = {"train": {}, "all": {}}
        try:
            # 同一图片内按词表作用域分别缓存短语向量，避免跨作用域词表匹配。
            for key, evidence in evidence_by_key.items():
                scope = split_scope_by_key[key]
                phrases = {item.phrase for item in evidence}
                if len(evidence_by_key) > 1:
                    phrases.update(parents[other_key][1] for other_key in image_keys)
                normalizers[scope].encode_image_phrases(phrases, vectors_by_scope[scope])
            image_results: list[CaptionResult] = []
            for key in sorted(evidence_by_key):
                scope = split_scope_by_key[key]
                vocabulary = vocabularies[scope]
                normalizer = normalizers[scope]
                parent_category, parent_attribute, parent_type = parents[key]
                parent = vocabulary.parent_slot(parent_category, parent_type, parent_attribute)
                result = normalizer.normalize_caption(
                    key[0], key[1], parent_category, parent, evidence_by_key[key],
                    vectors=vectors_by_scope[scope]
                )
                if args.llm_tail:
                    results.append(result)
                else:
                    unresolved_by_scope[scope] += len(result.unresolved)
                image_results.append(result)
            if not args.llm_tail:
                for result in image_results:
                    scope = split_scope_by_key[(result.image_name, result.caption)]
                    vocabulary = vocabularies[scope]
                    parent_info = parents[(result.image_name, result.caption)]
                    parent = vocabulary.parent_slot(parent_info[0], parent_info[2], parent_info[1])
                    assemble_budgeted_candidates(result, vocabulary, parent,
                                                 args.max_candidates_per_root)
                for result in image_results:
                    scope = split_scope_by_key[(result.image_name, result.caption)]
                    check_cross_caption_duplicates(
                        image_name, [result], phrases_by_caption, normalizers[scope],
                        vectors_by_scope[scope], args.cross_caption_duplicate_threshold
                    )
                for result in image_results:
                    payload.setdefault(image_name, {})[result.caption] = compact_root(result)
        finally:
            for scope, vectors in vectors_by_scope.items():
                normalizers[scope].release_image_vectors(vectors)
    if skipped:
        print(f"⚠️ 跳过 {skipped} 个在实例表里找不到父 caption 的组")

    processed_count = sum(len(captions) for captions in payload.values()) if not args.llm_tail else len(results)
    print(f"✅ 已归一化 {processed_count} 个 caption（规则级"
          f"{' + 向量级' if encoder else ''}）；train 根 {train_root_count}，"
          f"val/test 根 {all_root_count}")
    if encoder is not None:
        print("   常驻词表向量 " + "; ".join(
            f"{scope} {normalizer.vocabulary_vector_count} 条"
            for scope, normalizer in normalizers.items()
        ) + "；开放短语临时向量已释放")

    # ---- LLM 尾部 ----
    new_entries: list = []
    warnings: list[str] = []
    use_tail = args.llm_tail
    for scope in ("train", "all"):
        scoped_results = [result for result in results
                          if split_scope_by_key[(result.image_name, result.caption)] == scope]
        grouped = group_unresolved(scoped_results) if use_tail else {}
        scoped_unresolved = sum(len(items) for items in grouped.values()) if use_tail else unresolved_by_scope[scope]
        if scoped_unresolved and use_tail:
            print(f"⏳ {scope} 未解析短语 {scoped_unresolved} 个，交给 LLM 尾部")
            online = OnlineModel.create(concurrency=args.concurrency)
            decisions, scoped_new, scoped_warnings = await resolve_tails(
                online, vocabularies[scope], settings, args.tail_batch_size, grouped
            )
            warnings.extend(scoped_warnings)
            for entry in scoped_new:
                previous = {item.predicate_id: item for item in new_entries}.get(entry.predicate_id)
                if previous is not None and previous.slot.key() != entry.slot.key():
                    raise ValueError(f"train/all 生成词表的谓词 ID 冲突：{entry.predicate_id}")
                if previous is None:
                    new_entries.append(entry)
            scoped_new_by_id = {entry.predicate_id: entry for entry in scoped_new}
            for result in scoped_results:
                parent_info = parents[(result.image_name, result.caption)]
                parent = vocabularies[scope].parent_slot(parent_info[0], parent_info[2], parent_info[1])
                normalizers[scope].apply_tail(
                    result, parent, decisions,
                    scoped_new_by_id,
                )
            print(f"✅ {scope} 尾部：{len(decisions)} 个短语有决策，新增谓词 {len(scoped_new)} 个")
        elif scoped_unresolved:
            print(f"⚠️ {scope} 未解析短语 {scoped_unresolved} 个（默认关闭 LLM 尾部；需要时传 --llm-tail）")

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
            vectors_by_scope: dict[str, dict] = {"train": {}, "all": {}}
            try:
                for result in same_image:
                    scope = split_scope_by_key[(result.image_name, result.caption)]
                    normalizers[scope].encode_image_phrases(
                        (phrase for phrases in phrases_by_caption.values() for phrase in phrases),
                        vectors_by_scope[scope],
                    )
                    vocabulary = vocabularies[scope]
                    parent_info = parents[(image_name, result.caption)]
                    parent = vocabulary.parent_slot(parent_info[0], parent_info[2], parent_info[1])
                    assemble_budgeted_candidates(result, vocabulary, parent,
                                                 args.max_candidates_per_root)
                for result in same_image:
                    scope = split_scope_by_key[(result.image_name, result.caption)]
                    check_cross_caption_duplicates(
                        image_name, [result], phrases_by_caption, normalizers[scope],
                        vectors_by_scope[scope], args.cross_caption_duplicate_threshold
                    )
            finally:
                for scope, vectors in vectors_by_scope.items():
                    normalizers[scope].release_image_vectors(vectors)

    # ---- 紧凑输出 ----
    selected_new_ids: set[str] = set()
    if use_tail:
        for result in results:
            selected_new_ids.update(candidate.predicate_id for candidate in result.candidates
                                    if candidate.source == "generated")
            payload.setdefault(result.image_name, {})[result.caption] = compact_root(result)
    write_jsonl_atomic(args.output, (
        {"image_name": image_name, "caption": caption, **entry}
        for image_name, captions in payload.items()
        for caption, entry in captions.items()
    ))

    selected_new = [entry for entry in new_entries if entry.predicate_id in selected_new_ids]
    Vocabulary(entries=selected_new, metadata={
        "origin": "generated-by-step4-tail", "source_extract": str(args.input),
        "parent_vocabulary": {scope: str(args.vocabulary if scope == "train" else args.vocabulary_all)
                              for scope in ("train", "all")},
        "splits": str(args.splits), "count": len(selected_new),
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
    for name in ("input", "instances", "vocabulary", "vocabulary_all", "splits"):
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
    if {args.output.resolve(), args.generated_vocabulary.resolve()} & {
        args.vocabulary.resolve(), args.vocabulary_all.resolve(), args.splits.resolve(),
        args.instances.resolve()}:
        print("❌ 输出路径不能覆盖划分、实例表或原始词表", file=sys.stderr)
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
