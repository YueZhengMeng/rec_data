"""冻结、分发并校验 REC-8K 多人审核任务，不重新生成子集 ID 或图片索引。"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from recsub.io import (
    iter_jsonl, read_run_fingerprint, run_marker_path, write_json_atomic, write_run_fingerprint,
)

MANIFEST_NAME = "review_manifest.json"
BATCH_MANIFEST_NAME = "review_batch.json"
SCHEMA_VERSION = 1
TASK_FILES = {"review_items": "review_items.jsonl", "state": "review_state.jsonl",
              "final_output": "final_subsets.jsonl"}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def dataset_fingerprint(items: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(items, key=lambda item: (item["image_idx"], item.get("caption", ""), item["review_id"]))
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


def task_fingerprint(dataset_id: str, task_id: str, start: int, end: int,
                     items_sha256: str) -> str:
    return hashlib.sha256(canonical_json({
        "schema_version": SCHEMA_VERSION, "dataset_id": dataset_id, "task_id": task_id,
        "start": start, "end": end, "items_sha256": items_sha256,
    })).hexdigest()


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return value


def _validate_items(items: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    ids: set[str] = set()
    image_names: dict[int, str] = {}
    image_indices: dict[str, int] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("审核清单条目必须是 JSON 对象")
        review_id = item.get("review_id")
        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError("审核清单存在空 review_id")
        if review_id in ids:
            raise ValueError(f"审核清单存在重复 review_id：{review_id}")
        ids.add(review_id)
        image_idx = _nonnegative_int(item.get("image_idx"), f"{review_id} 的 image_idx")
        image_name = item.get("image_name")
        if not isinstance(image_name, str) or not image_name:
            raise ValueError(f"{review_id} 缺少图片名")
        if image_names.setdefault(image_idx, image_name) != image_name:
            raise ValueError(f"image_idx={image_idx} 对应多个图片")
        if image_indices.setdefault(image_name, image_idx) != image_idx:
            raise ValueError(f"图片 {image_name} 对应多个 image_idx")
    return image_names


def task_paths(task_dir: Path) -> dict[str, Path]:
    """Use fixed, local file names; manifests cannot redirect reads/writes outside the task."""
    paths = {key: task_dir / name for key, name in TASK_FILES.items()}
    for output in [*paths.values(), *(run_marker_path(paths[key]) for key in ("state", "final_output"))]:
        if output.is_symlink() or output.resolve().parent != task_dir.resolve():
            raise ValueError(f"任务文件不能指向任务目录之外：{output}")
    return paths


def _check_batch_directory(output_dir: Path) -> None:
    if output_dir.is_symlink() or (output_dir.exists() and (
            not output_dir.is_dir() or any(output_dir.iterdir()))):
        raise FileExistsError(f"任务批次路径不是空目录，拒绝覆盖：{output_dir}")


def prepare_review_tasks(
        items: Sequence[Mapping[str, Any]], output_dir: str | Path, task_count: int,
        images_per_task: int, write_artifacts: Callable,
        *, start: int = 0, sources: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build all task packages in a new staging directory, then publish the batch."""
    if type(task_count) is not int or task_count <= 0:
        raise ValueError("--prepare-tasks 必须是正整数")
    if type(images_per_task) is not int or images_per_task <= 0:
        raise ValueError("--images-per-task 必须是正整数")
    _nonnegative_int(start, "任务起点")
    output_dir = Path(output_dir)
    _check_batch_directory(output_dir)
    image_names = _validate_items(items)
    ordered_items = sorted(items, key=lambda item: (item["image_idx"], item.get("caption", ""), item["review_id"]))
    total_end = start + task_count * images_per_task
    uncovered = sorted(index for index in image_names if not start <= index < total_end)
    if uncovered:
        raise ValueError(f"任务区间 [{start}, {total_end}) 未覆盖 image_idx：{uncovered[:10]}；请增加任务数或区间长度")
    dataset_id = dataset_fingerprint(ordered_items)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        source_dir = temporary / ".source"
        source_items, source_csv = write_artifacts(ordered_items, source_dir)
        items_sha256 = sha256_file(source_items)
        tasks = []
        for number in range(task_count):
            task_id = f"review_{number + 1:02d}"
            task_start = start + number * images_per_task
            task_end = task_start + images_per_task
            assigned = [item for item in ordered_items if task_start <= item["image_idx"] < task_end]
            indices = sorted({item["image_idx"] for item in assigned})
            task_dir = temporary / task_id
            task_dir.mkdir()
            shutil.copyfile(source_items, task_dir / TASK_FILES["review_items"])
            shutil.copyfile(source_csv, task_dir / "review_sheet.csv")
            fingerprint = task_fingerprint(dataset_id, task_id, task_start, task_end, items_sha256)
            manifest = {
                "schema_version": SCHEMA_VERSION, "dataset_id": dataset_id,
                "items_sha256": items_sha256, "task_id": task_id,
                "start": task_start, "end": task_end, "item_count": len(assigned),
                "image_count": len(indices), "image_indices": indices,
                "all_image_indices": sorted(image_names), "fingerprint": fingerprint,
                "sources": dict(sources or {}), **TASK_FILES,
            }
            write_json_atomic(task_dir / MANIFEST_NAME, manifest, indent=2)
            tasks.append({"task_id": task_id, "directory": task_id, **{
                key: manifest[key] for key in ("start", "end", "item_count", "image_count", "fingerprint")
            }})
        shutil.rmtree(source_dir)
        batch = {
            "schema_version": SCHEMA_VERSION, "dataset_id": dataset_id, "items_sha256": items_sha256,
            "task_count": task_count, "images_per_task": images_per_task,
            "start": start, "end": total_end, "item_count": len(ordered_items),
            "image_count": len(image_names), "sources": dict(sources or {}), "tasks": tasks,
        }
        write_json_atomic(temporary / BATCH_MANIFEST_NAME, batch, indent=2)
        _check_batch_directory(output_dir)
        had_empty_directory = output_dir.exists()
        if had_empty_directory:
            output_dir.rmdir()  # Only remove an empty directory; never delete existing task files.
        try:
            temporary.rename(output_dir)
        except BaseException:
            if had_empty_directory and not output_dir.exists():
                output_dir.mkdir()
            raise
        return batch
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_and_validate_task(task_dir: str | Path) -> tuple[list[dict], dict[str, Any], str]:
    """Validate frozen content and task metadata without changing any files."""
    task_dir = Path(task_dir)
    with (task_dir / MANIFEST_NAME).open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != SCHEMA_VERSION):
        raise ValueError("不支持的审核任务 manifest schema_version")
    required = ("dataset_id", "items_sha256", "task_id", "start", "end", "item_count",
                "image_count", "image_indices", "all_image_indices", "fingerprint", *TASK_FILES)
    if any(key not in manifest for key in required):
        raise ValueError("审核任务 manifest 缺少字段")
    for key, name in TASK_FILES.items():
        if manifest[key] != name:
            raise ValueError(f"审核任务 manifest 的 {key} 必须为 {name}")
    paths = task_paths(task_dir)
    actual_hash = sha256_file(paths["review_items"])
    if actual_hash != manifest["items_sha256"]:
        raise ValueError("审核清单版本校验失败：review_items.jsonl 已被修改或不是同一批次")
    items = list(iter_jsonl(paths["review_items"]))
    image_names = _validate_items(items)
    if dataset_fingerprint(items) != manifest["dataset_id"]:
        raise ValueError("审核清单 dataset_id 版本校验失败")
    start = _nonnegative_int(manifest["start"], "任务起点")
    end = _nonnegative_int(manifest["end"], "任务终点")
    if end <= start:
        raise ValueError("审核任务 manifest 的区间非法")
    assigned = [item for item in items if start <= item["image_idx"] < end]
    indices = sorted({item["image_idx"] for item in assigned})
    if indices != manifest["image_indices"] or sorted(image_names) != manifest["all_image_indices"]:
        raise ValueError("审核任务 manifest 的 image_idx 映射与清单不一致")
    if _nonnegative_int(manifest["item_count"], "子集数") != len(assigned):
        raise ValueError("审核任务子集数量校验失败")
    if _nonnegative_int(manifest["image_count"], "图片数") != len(indices):
        raise ValueError("审核任务图片数量校验失败")
    if not isinstance(manifest["task_id"], str) or not manifest["task_id"]:
        raise ValueError("审核任务 task_id 不能为空")
    expected = task_fingerprint(manifest["dataset_id"], manifest["task_id"], start, end, actual_hash)
    if expected != manifest["fingerprint"]:
        raise ValueError("审核任务 fingerprint 校验失败")
    return items, manifest, expected


def validate_task_state(states: Mapping[str, Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> None:
    item_by_id = {item["review_id"]: item for item in items}
    for review_id, state in states.items():
        item = item_by_id.get(review_id)
        if item is None:
            raise ValueError(f"审核状态包含不属于当前任务的 review_id：{review_id}")
        if not isinstance(state, Mapping):
            raise ValueError(f"审核状态 {review_id} 必须是 JSON 对象")
        if "status" in state and state["status"] not in ("pending", "keep", "discard", "unknown"):
            raise ValueError(f"审核状态 {review_id} 的 status 非法")
        valid_ids = {str(value) for value in (
            *item.get("yes_ids", ()), *item.get("no_ids", ()), *item.get("unknown_ids", ()))}
        labels = state.get("labels", {})
        if not isinstance(labels, Mapping) or any(
                str(key) not in valid_ids or value not in ("yes", "no") for key, value in labels.items()):
            raise ValueError(f"审核状态 {review_id} 含有不属于该子集的实例标签或非法 yes/no 值")


def validate_task_outputs(task_dir: Path, manifest: Mapping[str, Any],
                          assigned: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """Check both output markers and all existing records before claiming either output."""
    paths = task_paths(task_dir)
    fingerprint = manifest["fingerprint"]
    for key in ("state", "final_output"):
        output = paths[key]
        marker = run_marker_path(output)
        if marker.exists():
            if read_run_fingerprint(output) != fingerprint:
                raise ValueError(f"审核结果版本/任务 fingerprint 不匹配：{output}；请使用原任务目录")
        elif output.exists() and output.stat().st_size:
            raise ValueError(f"已有审核结果缺少版本标记，不能自动认领：{output}")
    states = {}
    if paths["state"].exists():
        for row in iter_jsonl(paths["state"]):
            if not isinstance(row, dict) or not isinstance(row.get("review_id"), str) or not row["review_id"]:
                raise ValueError("审核状态存在无效 review_id")
            validate_task_state({row["review_id"]: row}, assigned)
            states[row["review_id"]] = row
    if paths["final_output"].exists():
        valid_ids = {item["review_id"] for item in assigned}
        for row in iter_jsonl(paths["final_output"]):
            if not isinstance(row, dict) or row.get("review_id") not in valid_ids:
                raise ValueError("最终结果包含不属于当前任务的 review_id")
    return states


def claim_task_outputs(task_dir: Path, manifest: Mapping[str, Any],
                       assigned: Sequence[Mapping[str, Any]]) -> None:
    validate_task_outputs(task_dir, manifest, assigned)
    paths = task_paths(task_dir)
    for key in ("state", "final_output"):
        if not run_marker_path(paths[key]).exists():
            write_run_fingerprint(paths[key], manifest["fingerprint"])
