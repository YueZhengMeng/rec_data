"""按图片切分 step4 归一化任务，并将多个 GPU worker 的结果合并。

示例（Bash / WSL2）：
    python step4_normalize_predicates_parallel.py --gpus 0,1,2,3 \\
        --input anno/extract_result_local.jsonl

每张图片只交给一个独立 step4 进程，保证同图 caption 仍由原逻辑共同处理。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.instances import enrich_with_instances
from recsub.io import iter_jsonl, write_json_atomic, write_jsonl_atomic

_STEP4 = Path(__file__).with_name("step4_normalize_predicates.py")


def parse_gpus(value: str) -> list[str]:
    gpus = [item.strip() for item in value.split(",") if item.strip()]
    if not gpus:
        raise argparse.ArgumentTypeError("--gpus 至少需要一个 CUDA 设备编号")
    if len(set(gpus)) != len(gpus):
        raise argparse.ArgumentTypeError("--gpus 不能包含重复设备")
    return gpus


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    normalize = CONFIG["normalize"]
    parser = argparse.ArgumentParser(
        description="将 step4 按完整图片分片到多张 GPU 执行；图片数可能不能完全均衡"
    )
    parser.add_argument("--gpus", type=parse_gpus, required=True,
                        help="GPU 编号，逗号分隔，例如 0,1,2,3")
    parser.add_argument("--input", type=Path, default=path("extract_result_online"))
    parser.add_argument("--instances", type=Path, default=path("instance_data"))
    parser.add_argument("--vocabulary", type=Path, default=path("vocabulary_train"))
    parser.add_argument("--vocabulary-all", type=Path, default=path("vocabulary_all"))
    parser.add_argument("--splits", type=Path, default=path("splits"))
    parser.add_argument("--output", type=Path, default=path("predicate_candidates_online"))
    parser.add_argument("--max-candidates-per-root", type=int,
                        default=normalize["max_candidates_per_root"])
    parser.add_argument("--embedding", action=argparse.BooleanOptionalAction,
                        default=normalize["use_embedding"])
    parser.add_argument("--dedup-similarity-threshold", type=float,
                        default=normalize["dedup_similarity_threshold"])
    parser.add_argument("--similarity-threshold", type=float,
                        default=normalize["similarity_threshold"])
    parser.add_argument("--similarity-margin", type=float,
                        default=normalize["similarity_margin"])
    parser.add_argument("--cross-caption-duplicate-threshold", type=float,
                        default=normalize["cross_caption_duplicate_threshold"])
    parser.add_argument("--allow-refinement-split", action=argparse.BooleanOptionalAction,
                        default=normalize["allow_refinement_split"])
    parser.add_argument("--keep-temp", action="store_true",
                        help="成功完成后也保留分片、中间结果与 worker 日志")
    return parser.parse_args(argv)


def shard_index(image_name: str, shard_count: int) -> int:
    """稳定分配图片，不依赖 Python 进程级随机化的 hash()。"""
    digest = hashlib.sha256(image_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def prepare_shards(input_path: Path, instances_path: Path, run_dir: Path,
                   shard_count: int) -> list[dict[str, Any]]:
    """流式 enrich 并写出 JSONL；每个图片及其全部记录只属于一个 shard。"""
    handles = []
    manifests = [{"images": set(), "captions": {}, "records": 0}
                 for _ in range(shard_count)]
    try:
        for index in range(shard_count):
            handles.append((run_dir / f"shard-{index:03d}.jsonl").open("w", encoding="utf-8"))
        records = enrich_with_instances(iter_jsonl(input_path), instances_path)
        for record in records:
            image_name = str(record["image_name"])
            index = shard_index(image_name, shard_count)
            handles[index].write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            manifests[index]["images"].add(image_name)
            manifests[index]["captions"].setdefault(image_name, set()).add(str(record["caption"]))
            manifests[index]["records"] += 1
    finally:
        for handle in handles:
            handle.close()

    for index, manifest in enumerate(manifests):
        manifest["images"] = sorted(manifest["images"])
        manifest["captions"] = {
            image: sorted(captions) for image, captions in sorted(manifest["captions"].items())
        }
        manifest["input"] = run_dir / f"shard-{index:03d}.jsonl"
    write_json_atomic(run_dir / "manifest.json", [
        {"shard": index, "record_count": item["records"], "images": item["images"],
         "captions": item["captions"]}
        for index, item in enumerate(manifests)
    ])
    return manifests


def worker_command(args: argparse.Namespace, manifest: Mapping[str, Any], index: int,
                   run_dir: Path) -> tuple[list[str], dict[str, str], Path]:
    candidate_path = run_dir / f"worker-{index:03d}-candidates.jsonl"
    log_path = run_dir / f"worker-{index:03d}.log"
    command = [
        sys.executable, str(_STEP4), "--input", str(manifest["input"]),
        "--instances", str(args.instances), "--vocabulary", str(args.vocabulary),
        "--vocabulary-all", str(args.vocabulary_all), "--splits", str(args.splits),
        "--output", str(candidate_path),
        "--max-candidates-per-root", str(args.max_candidates_per_root),
        "--dedup-similarity-threshold", str(args.dedup_similarity_threshold),
        "--similarity-threshold", str(args.similarity_threshold),
        "--similarity-margin", str(args.similarity_margin),
        "--cross-caption-duplicate-threshold", str(args.cross_caption_duplicate_threshold),
        "--embedding" if args.embedding else "--no-embedding",
        "--allow-refinement-split" if args.allow_refinement_split else "--no-allow-refinement-split",
    ]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = args.gpus[index]
    return command, env, log_path


def merge_worker_outputs(manifests: Sequence[Mapping[str, Any]],
                         run_dir: Path) -> list[dict]:
    """校验各 worker 的 JSONL 候选，并按 (图片, caption) 稳定合并。"""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    owned_images: set[str] = set()
    for index, manifest in enumerate(manifests):
        assigned = set(manifest["images"])
        overlap = owned_images & assigned
        if overlap:
            raise ValueError(f"分片图片归属重叠：{sorted(overlap)[:5]}")
        owned_images.update(assigned)

        candidates_path = run_dir / f"worker-{index:03d}-candidates.jsonl"
        if not candidates_path.is_file():
            raise FileNotFoundError(f"worker {index} 缺少候选输出")
        for record in iter_jsonl(candidates_path):
            if not isinstance(record, dict):
                raise ValueError(f"worker {index} 的候选记录必须是对象")
            image_name = record.get("image_name")
            caption = record.get("caption")
            if not isinstance(image_name, str) or not isinstance(caption, str):
                raise ValueError(
                    f"worker {index} 的候选记录缺少 image_name/caption：{record!r}")
            if image_name not in assigned:
                raise ValueError(f"worker {index} 输出了未分配图片：{image_name!r}")
            expected_captions = set(manifest["captions"].get(image_name, []))
            if caption not in expected_captions:
                raise ValueError(
                    f"worker {index} 图片 {image_name!r} 输出了未分配 caption：{caption!r}")
            if (image_name, caption) in seen:
                raise ValueError(f"多个 worker 输出了同一 caption：{image_name!r} / {caption!r}")
            seen.add((image_name, caption))
            merged.append(record)

    merged.sort(key=lambda record: (record["image_name"], record["caption"]))
    return merged


def run(args: argparse.Namespace) -> int:
    for name in ("input", "instances", "vocabulary", "vocabulary_all", "splits"):
        if not getattr(args, name).is_file():
            raise FileNotFoundError(f"找不到 {name}：{getattr(args, name)}")
    if args.max_candidates_per_root < 0:
        raise ValueError("--max-candidates-per-root 必须 ≥0")
    source_paths = {args.input.resolve(), args.instances.resolve(), args.vocabulary.resolve(),
                    args.vocabulary_all.resolve(), args.splits.resolve()}
    if args.output.resolve() in source_paths:
        raise ValueError("输出路径不能覆盖输入、实例表或原始词表")

    run_dir = Path(tempfile.mkdtemp(prefix="step4-parallel-"))
    print(f"运行目录：{run_dir}")
    try:
        manifests = prepare_shards(args.input, args.instances, run_dir, len(args.gpus))
        active = [index for index, item in enumerate(manifests) if item["records"]]
        print("分片：" + "；".join(
            f"GPU {args.gpus[i]}: {manifests[i]['records']} 条 / {len(manifests[i]['images'])} 图"
            for i in active
        ))

        def invoke(index: int) -> tuple[int, int, Path]:
            command, env, log_path = worker_command(args, manifests[index], index, run_dir)
            with log_path.open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           check=False)
            return index, completed.returncode, log_path

        failures = []
        if active:
            with ThreadPoolExecutor(max_workers=len(active)) as pool:
                futures = [pool.submit(invoke, index) for index in active]
                for future in as_completed(futures):
                    index, return_code, log_path = future.result()
                    if return_code:
                        failures.append((index, return_code, log_path))
        if failures:
            details = "; ".join(
                f"GPU {args.gpus[i]} exit={code}, log={log}" for i, code, log in failures
            )
            raise RuntimeError(f"step4 worker 失败；未合并，运行目录保留：{details}")

        # 空分片不启动 worker，补齐与 manifest 索引对应的合法空输出。
        for index in range(len(manifests)):
            if index not in active:
                write_jsonl_atomic(run_dir / f"worker-{index:03d}-candidates.jsonl", [])
        merged_candidates = merge_worker_outputs(manifests, run_dir)

        # 合并结果先写成临时文件；只有 worker 与合并校验全部成功后才发布。
        staged_candidates = args.output.with_name(args.output.name + ".parallel.tmp")
        try:
            write_jsonl_atomic(staged_candidates, merged_candidates)
            os.replace(staged_candidates, args.output)
        finally:
            staged_candidates.unlink(missing_ok=True)

        image_count = len({record["image_name"] for record in merged_candidates})
        print(f"✅ 合并完成：{image_count} 张图片 / {len(merged_candidates)} 条 caption "
              f"→ {args.output}")
        if args.keep_temp:
            print(f"中间文件保留：{run_dir}")
        else:
            shutil.rmtree(run_dir)
        return 0
    except Exception:
        print(f"运行未完成；中间文件保留用于诊断：{run_dir}", file=sys.stderr)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"❌ 并行归一化失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
