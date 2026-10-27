"""step9 人工语言审核：历史记录原地确认，手工修改追加新记录。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from recsub.caption import accepted_caption, generated_caption, string_checks
from recsub.export import load_nodes
from recsub.io import iter_json_records, write_jsonl_atomic

_CONTEXT = ("image_name", "caption", "category", "original_attributes", "additional_attribute")


def record_revision(record: Mapping[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_context(record: Mapping[str, Any]) -> None:
    for field in ("node_id", *_CONTEXT):
        value = record.get(field)
        if not isinstance(value, str) or (field != "original_attributes" and not value.strip()):
            raise ValueError(f"step9 记录缺少有效的 {field}：{record.get('node_id')}")


def approve_caption(record: Mapping[str, Any], attributes: str) -> dict:
    """人工确认只校验结构，不将字符串 in=no 当作拒绝条件。"""
    _validate_context(record)
    if not isinstance(attributes, str):
        raise ValueError("新属性短语必须是字符串")
    phrase = attributes.strip()
    if not phrase or len(phrase.splitlines()) != 1 or len(phrase) > 4096:
        raise ValueError("请填写非空、单行且不超过 4096 字符的新属性短语")
    result = dict(record)
    result.update(generated_caption(record["category"], phrase))
    result.update(string_checks({"attributes": record["original_attributes"],
                                 "additional_attribute": record["additional_attribute"]}, result))
    result.update(accepted=True, language_check="yes", language_check_source="human")
    result["manual_review"] = {
        "status": "yes", "edited": phrase != record.get("new_attributes"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "previous_language_check": record.get("language_check"),
        "previous_failed": bool(record.get("failed")), "previous_reason": record.get("reason"),
        "previous_new_attributes": record.get("new_attributes"),
        "previous_new_caption": record.get("new_caption"),
    }
    result.pop("failed", None)
    result.pop("reason", None)
    return result


def _stamp(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class CaptionReviewSession:
    """单人单文件审核，历史候选和人工结果始终留在同一份完整 step9 结果中。"""

    def __init__(self, input_path: Path):
        self.input_path = input_path.resolve()
        if not self.input_path.is_file():
            raise FileNotFoundError(f"找不到 step9 输入：{self.input_path}")
        self.lock = threading.Lock()
        self.stamp = _stamp(self.input_path)
        latest = load_nodes(self.input_path, latest=True)
        records = []
        for node_id, record in latest.items():
            if accepted_caption(record):
                continue
            record = {**record, "node_id": node_id}
            _validate_context(record)
            records.append(record)
        records.sort(key=lambda record: (record.get("image_idx") if type(record.get("image_idx")) is int
                                         else 2 ** 63, record["image_name"], record["caption"], record["node_id"]))
        self.items = {record["node_id"]: record for record in records}
        self.histories: dict[str, list[dict]] = {node_id: [] for node_id in self.items}
        self.locations: dict[str, dict] = {}
        self.record_count = 0
        for index, record in enumerate(iter_json_records(self.input_path), 1):
            self.record_count = index
            node_id = record.get("node_id") or record.get("review_id")
            current = self.items.get(node_id)
            if current is None or any(record.get(key) != current.get(key) for key in _CONTEXT):
                continue
            location = {"record_index": index, "record_revision": record_revision(record)}
            if location["record_revision"] == record_revision(current):
                self.locations[node_id] = location
            self._add_candidate(node_id, record, location)
        self._check_unchanged()

    def _add_candidate(self, node_id: str, record: dict, location: dict) -> None:
        phrase = record.get("new_attributes")
        if not isinstance(phrase, str) or not phrase.strip():
            return
        phrase = phrase.strip()
        candidates = self.histories[node_id]
        candidate = next((row for row in candidates if row["new_attributes"] == phrase), None)
        if candidate is None:
            candidate = {**generated_caption(record["category"], phrase), "attempts": []}
            candidates.append(candidate)
        candidate["attempts"].append({**location, "source": str(self.input_path),
                                      "language_check": record.get("language_check"),
                                      "string_check": record.get("string_check"),
                                      "failed": bool(record.get("failed")), "reason": record.get("reason"),
                                      "manual": record.get("language_check_source") == "human"})

    def _check_unchanged(self) -> None:
        if _stamp(self.input_path) != self.stamp:
            raise RuntimeError("结果文件已被其他进程修改。请停止其他 step9/分离进程，再重启审核脚本")

    def _view(self, record: dict) -> dict:
        fields = ("node_id", *_CONTEXT, "new_attributes", "new_caption", "language_check",
                  "string_check", "failed", "reason", "manual_review", "complement_of")
        return {**{key: record.get(key) for key in fields},
                "accepted": accepted_caption(record), "revision": record_revision(record),
                "member_count": len(record.get("instance_ids") or []),
                "current_record": self.locations.get(record["node_id"]),
                "candidates": self.histories[record["node_id"]]}

    def snapshot(self) -> dict:
        with self.lock:
            self._check_unchanged()
            items = [self._view(record) for record in self.items.values()]
            return {"items": items, "total": len(items),
                    "approved": sum(item["accepted"] for item in items), "input": str(self.input_path)}

    def approve(self, node_id: str, attributes: str, revision: str, selection: dict | None = None) -> dict:
        with self.lock:
            self._check_unchanged()
            current = self.items.get(node_id)
            if current is None:
                raise ValueError("未知或不在当前待审核列表内的 node_id")
            if revision != record_revision(current):
                raise ValueError("当前条目已被修改，请刷新页面后再确认")
            if selection is not None:
                if not isinstance(selection, dict):
                    raise ValueError("历史选择必须包含记录序号和版本")
                index, version = selection.get("record_index"), selection.get("record_revision")
                valid = any(attempt["record_index"] == index and attempt["record_revision"] == version
                            for candidate in self.histories[node_id] for attempt in candidate["attempts"])
                if type(index) is not int or not valid:
                    raise ValueError("历史记录不属于此条目，或选择的版本无效")
                approved = None

                def rewritten():
                    nonlocal approved
                    for position, record in enumerate(iter_json_records(self.input_path), 1):
                        if position == index:
                            if record_revision(record) != version:
                                raise ValueError("历史记录已改变，请重新载入")
                            if not isinstance(attributes, str) or attributes.strip() != record[
                                "new_attributes"].strip():
                                raise ValueError("历史文本已被修改，请按新数据保存")
                            approved = approve_caption(record, record["new_attributes"])
                            record = approved
                        yield record
                    if approved is None:
                        raise ValueError("找不到选择的历史记录")
                    self._check_unchanged()

                write_jsonl_atomic(self.input_path, rewritten())
                mode = "updated_existing"
                # 更新缓存中这一历史记录的状态，其他历史完全保留。
                for candidate in self.histories[node_id]:
                    candidate["attempts"][:] = [attempt for attempt in candidate["attempts"]
                                                if attempt["record_index"] != index]
            else:
                approved = approve_caption(current, attributes)
                approved["manual_review"]["save_mode"] = "new_record"
                encoded = (json.dumps(approved, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                with self.input_path.open("a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell():
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            handle.write(b"\n")
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.record_count += 1
                index = self.record_count
                mode = "appended_new"
            self.stamp = _stamp(self.input_path)
            location = {"record_index": index, "record_revision": record_revision(approved)}
            self.locations[node_id] = location
            self._add_candidate(node_id, approved, location)
            self.items[node_id] = approved
            return {**self._view(approved), "save_mode": mode}
