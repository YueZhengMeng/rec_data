"""把 step3 提取 JSONL 中的失败记录分离到独立文件。

输入默认是 ``anno/extract_result.jsonl``（在线结果）；将 ``failed: true`` 的记录
写入同目录的 ``extract_result_failed.jsonl``，并原子替换输入文件，使输入中仅保留
成功记录。本地结果可用 ``--input anno/extract_result_local.jsonl`` 指定。

    python step3_split_failed_extractions.py
    python step3_split_failed_extractions.py --input anno/extract_result_local.jsonl \\
        --failed-output anno/extract_result_local_failed.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from recsub.config import path
from recsub.console import configure_console
from recsub.io import ensure_parent, iter_jsonl


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分离提取失败记录，原 JSONL 保留成功记录")
    parser.add_argument("--input", type=Path, default=path("extract_result_online"),
                        help="待清理的 step3 提取 JSONL")
    parser.add_argument("--failed-output", type=Path, default=None,
                        help="失败记录输出；默认在输入文件名后加 _failed")
    return parser.parse_args(argv)


def split_failures(input_path: Path, failed_path: Path) -> tuple[int, int]:
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
                is_failed = bool(record.get("failed"))
                handle = failed_handle if is_failed else success_handle
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                if is_failed:
                    failed += 1
                else:
                    succeeded += 1
            for handle in (success_handle, failed_handle):
                handle.flush()
                os.fsync(handle.fileno())

        # 先发布失败备份，再替换原文件；若最后一步失败，原始记录仍在输入中。
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
        succeeded, failed = split_failures(args.input, failed_output)
    except Exception as error:
        print(f"❌ 分离失败：{error}", file=sys.stderr)
        return 1
    print(f"✅ 原文件保留成功记录 {succeeded} 条：{args.input}")
    print(f"✅ 失败记录 {failed} 条：{failed_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
