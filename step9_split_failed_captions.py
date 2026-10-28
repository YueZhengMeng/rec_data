"""把 step9 JSONL 中未成功的记录分离到独立文件。

成功记录原地保留；调用/解析失败、语言审核为 no、未审核或结构不完整的记录
写入 failed-output，便于再次运行 step9 时只处理这些记录。

    python step9_split_failed_captions.py
    python step9_split_failed_captions.py --input anno/subset_captions_local.jsonl \
        --failed-output anno/subset_captions_local_failed.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from recsub.caption import valid_generated_record
from recsub.config import path
from recsub.console import configure_console
from recsub.io import ensure_parent, iter_jsonl


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="分离 step9 失败/审核 no 记录，原 JSONL 保留成功记录"
    )
    parser.add_argument("--input", type=Path, default=path("captions_online"),
                        help="待清理的 step9 合成结果 JSONL")
    parser.add_argument("--failed-output", type=Path, default=None,
                        help="失败或审核 no 的记录输出；默认在输入文件名后加 _failed")
    return parser.parse_args(argv)


def is_success(record: dict) -> bool:
    """只有可供 step10 使用的 accepted=yes 记录才留在原文件。"""
    return (not record.get("failed")
            and record.get("accepted") is True
            and str(record.get("language_check") or "").lower() == "yes"
            and valid_generated_record(record))


def split_failed_captions(input_path: Path, failed_path: Path) -> tuple[int, int]:
    input_path = input_path.resolve()
    failed_path = failed_path.resolve()
    if input_path == failed_path:
        raise ValueError("--input 与 --failed-output 必须是不同文件")
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入文件：{input_path}")

    ensure_parent(input_path)
    ensure_parent(failed_path)
    success_temp = input_path.with_name(input_path.name + ".success.tmp")
    failed_temp = failed_path.with_name(failed_path.name + ".tmp")
    succeeded = failed = 0
    try:
        with success_temp.open("w", encoding="utf-8", newline="\n") as success_handle, \
                failed_temp.open("w", encoding="utf-8", newline="\n") as failed_handle:
            for record in iter_jsonl(input_path):
                is_failed = not is_success(record)
                handle = failed_handle if is_failed else success_handle
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                if is_failed:
                    failed += 1
                else:
                    succeeded += 1
            for handle in (success_handle, failed_handle):
                handle.flush()
                os.fsync(handle.fileno())

        # 先发布待重跑记录，再替换原文件；任一步失败都清理临时文件。
        os.replace(failed_temp, failed_path)
        os.replace(success_temp, input_path)
    except BaseException:
        success_temp.unlink(missing_ok=True)
        failed_temp.unlink(missing_ok=True)
        raise
    return succeeded, failed


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    failed_output = args.failed_output or args.input.with_name(
        f"{args.input.stem}_failed{args.input.suffix or '.jsonl'}"
    )
    try:
        succeeded, failed = split_failed_captions(args.input, failed_output)
    except Exception as error:
        print(f"❌ 分离失败：{error}", file=sys.stderr)
        return 1
    print(f"✅ 原文件保留成功记录 {succeeded} 条：{args.input}")
    print(f"✅ 失败/审核 no/未审核记录 {failed} 条：{failed_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
