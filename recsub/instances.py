"""实例表的读取、父 caption 索引，以及与提取产物的配对。

只带 ``instance_id`` 与 ``attributes``、没有图片名/caption/类别的提取记录（例如
记录数组形式 JSON 迁移后的产物）同样可用：这里做一层归并式配对，靠 ``instance_id``
与实例表对齐。两份文件都按 ``instance_id`` 递增排列，所以内存是 O(1)，不必先建
28.6 万条的索引字典。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Mapping

from recsub.io import iter_jsonl

_IMAGE_FIELDS = ("image_name", "caption", "category")


def load_parents(instance_path: str | Path) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """(图片, caption) → (类别, 原属性, type)。

    同一个根内这些字段必然一致，取首次出现即可。用于查父 caption 自身的槽位，
    也就是 step4 里硬过滤「与父属性同义」的候选时要比对的那个槽位。
    """
    parents: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for instance in iter_jsonl(instance_path):
        key = (str(instance["image_name"]), str(instance["caption"]))
        if key not in parents:
            parents[key] = (str(instance["category"]), str(instance["attributes"]),
                            instance.get("type"))
    return parents


def enrich_with_instances(records: Iterator[Mapping[str, Any]],
                          instance_path: str | Path) -> Iterator[dict]:
    """把缺图片信息的提取记录补上 ``image_name`` / ``caption`` / ``category``。

    step3 产出的记录自带这些字段，原样透传。只带 ``instance_id`` 的记录按
    ``instance_id`` 与实例表归并配对；``instance_id`` 重复出现时复用上一次的配对
    结果，这样带重复行的提取文件也能跑。
    """
    instances = iter_jsonl(instance_path)
    pending: Mapping[str, Any] | None = next(instances, None)
    last_id: int | None = None
    last_instance: Mapping[str, Any] | None = None

    for record in records:
        if all(field in record for field in _IMAGE_FIELDS):
            yield dict(record)
            continue

        instance_id = record.get("instance_id")
        if not isinstance(instance_id, int):
            raise ValueError(
                f"提取记录既没有图片信息也没有可用的 instance_id：{str(record)[:160]}"
            )

        if last_id is not None and instance_id == last_id and last_instance is not None:
            matched = last_instance
        else:
            if last_id is not None and instance_id < last_id:
                raise ValueError(
                    f"提取结果未按 instance_id 递增（{instance_id} 出现在 {last_id} 之后），"
                    f"无法与实例表归并配对；请重新生成提取结果"
                )
            while pending is not None and pending["instance_id"] < instance_id:
                pending = next(instances, None)
            if pending is None or pending["instance_id"] != instance_id:
                raise ValueError(
                    f"实例表里找不到 instance_id={instance_id}；"
                    f"请确认 --instances 与提取结果来自同一次 step1"
                )
            matched = pending
            last_id, last_instance = instance_id, matched

        yield {
            **record,
            "image_name": matched["image_name"],
            "caption": matched["caption"],
            "category": matched["category"],
            "type": matched.get("type"),
            "point": matched["point"],
        }
