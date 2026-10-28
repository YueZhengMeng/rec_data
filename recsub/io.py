"""JSONL 读写、原子写入、运行标记，以及记录数组形式 JSON 的流式迁移。

为什么统一用 JSONL
------------------
实例表和提取结果都有数十万条记录。若每写一条结果都要「读回整个文件 → 追加 →
整体写回」，IO 会随记录数呈 O(n²) 增长，先于 API 成为瓶颈；追加一行 JSONL 只写
一行，且天然支持中断续跑。

:func:`load_json_any` 按后缀自动选择读取器，``.json`` 产物不转换也能直接读；
``python -m recsub.io migrate`` 则把记录数组形式的 JSON 转成 JSONL（流式解析，
写出前校验条数）。

断续跑所需的运行指纹不写在每一条记录里，而是旁挂一个 ``.run.json`` 标记文件，
见 :func:`run_marker_path`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

# 项目内 JSONL 记录一律紧凑、不转义非 ASCII，保证中文可读。
_JSONL_KWARGS: dict[str, Any] = {"ensure_ascii": False, "separators": (",", ":")}
_CHUNK_SIZE = 1 << 20
_WHITESPACE = " \t\r\n"


# --------------------------------------------------------------------------
# 读
# --------------------------------------------------------------------------
def iter_jsonl(path: str | Path, *, strict: bool = True) -> Iterator[dict]:
    """流式逐行读取 JSONL。

    内存占用与文件大小无关，因此可以直接读数百 MB 的提取结果。
    ``strict=False`` 时跳过损坏行并打印告警，用于容忍被中断的追加写入。
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as error:
                if strict:
                    raise ValueError(f"{path} 第 {line_number} 行不是合法 JSON: {error}") from error
                print(f"⚠️ 跳过损坏行：{path} 第 {line_number} 行", file=sys.stderr)


def load_jsonl(path: str | Path) -> list[dict]:
    """把整个 JSONL 读成列表；仅用于小文件（候选、词表、审核条目）。"""
    return list(iter_jsonl(path))


def count_jsonl(path: str | Path) -> int:
    """统计非空行数，用于校验迁移结果。"""
    total = 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                total += 1
    return total


def load_json_any(path: str | Path) -> Any:
    """按后缀自动选择 JSON 或 JSONL 读取器，``.json`` 产物不用转换即可使用。"""
    path = Path(path)
    if path.suffix.lower() == ".jsonl":
        return load_jsonl(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _iter_json_array_stream(handle, *, label: str = "<stream>") -> Iterator[Any]:
    """流式解析顶层 JSON 数组，避免把数百 MB 一次性读进内存。

    做法是维持一个滑动缓冲区：每次成功解析一个元素后就丢弃已消费的前缀，
    缓冲区只在单个元素超过一个 chunk 时才增长。
    """
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False

    def read_more() -> bool:
        """读入下一块并丢弃已消费前缀；返回 False 表示到文件末尾。"""
        nonlocal buffer, position, eof
        chunk = handle.read(_CHUNK_SIZE)
        if not chunk:
            eof = True
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        buffer += chunk
        return True

    def ensure() -> None:
        while len(buffer) - position < 1 and not eof:
            read_more()

    # 定位数组起始标记，顺带给出「顶层不是数组」的明确报错。
    while True:
        ensure()
        rest = buffer[position:].lstrip(_WHITESPACE)
        if rest:
            if rest[0] != "[":
                raise ValueError(f"{label}: 顶层不是 JSON 数组，无法迁移为 JSONL")
            position = len(buffer) - len(rest) + 1
            break
        if eof:
            raise ValueError(f"{label}: 文件为空")

    while True:
        ensure()
        rest = buffer[position:]
        if not rest:
            if eof:
                raise ValueError(f"{label}: 数组未闭合")
            continue
        char = rest[0]
        # 空白和逗号都当分隔符跳过，因此也容忍尾随逗号。
        if char in _WHITESPACE or char == ",":
            position += 1
            continue
        if char == "]":
            return
        try:
            value, end = decoder.raw_decode(buffer, position)
        except json.JSONDecodeError:
            if eof:
                raise ValueError(f"{label}: 偏移 {position} 处的元素无法解析") from None
            if not read_more():
                raise ValueError(f"{label}: 数组未闭合")
            continue
        yield value
        position = end


def iter_json_records(path: str | Path) -> Iterator[Any]:
    """流式读取任意写法的 JSON 记录文件，内存占用与文件大小无关。

    兼容三种写法：

    * 一行一条的 JSONL；
    * 顶层记录数组 ``[{...}, {...}]``（``migrate_json_to_jsonl`` 的输入）；
    * 缩进 JSON 记录首尾相接的「JSON 流」——每条记录 ``indent=2`` 后直接续写，
      文件后缀仍可能是 ``.jsonl``，逐行解析会失败。

    源文件数百 MB 也不会整体载入：维持滑动缓冲区，每解析出一条记录就丢弃
    已消费的前缀。记录之间允许空白（数组内额外允许逗号）。
    """
    label = str(path)
    with Path(path).open("r", encoding="utf-8") as handle:
        yield from _iter_json_value_stream(handle, label=label)


def _iter_json_value_stream(handle, *, label: str = "<stream>") -> Iterator[Any]:
    """``iter_json_records`` 的底层实现，供已经打开的文件句柄复用。"""
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False

    def read_more() -> bool:
        """读入下一块并丢弃已消费前缀；返回 False 表示到文件末尾。"""
        nonlocal buffer, position, eof
        chunk = handle.read(_CHUNK_SIZE)
        if not chunk:
            eof = True
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        buffer += chunk
        return True

    def skip_whitespace() -> str:
        """跳过前导空白并返回剩余内容；返回空串表示已到文件末尾。"""
        nonlocal position
        while True:
            rest = buffer[position:].lstrip(_WHITESPACE)
            if rest:
                position = len(buffer) - len(rest)
                return rest
            if eof:
                return ""
            position = len(buffer)
            read_more()

    # 首个非空白字符是 "[" 时按顶层数组读，否则按首尾相接的记录流读。
    rest = skip_whitespace()
    in_array = rest.startswith("[")
    if in_array:
        position += 1

    while True:
        while True:
            rest = buffer[position:]
            if rest:
                break
            if eof:
                if in_array:
                    raise ValueError(f"{label}: 数组未闭合")
                return
            read_more()

        char = rest[0]
        # 记录流里的记录之间是换行，数组里还有逗号；空白一律跳过。
        if char in _WHITESPACE or (in_array and char == ","):
            position += 1
            continue
        if in_array and char == "]":
            return
        try:
            value, end = decoder.raw_decode(buffer, position)
        except json.JSONDecodeError:
            if eof:
                raise ValueError(f"{label}: 偏移 {position} 处的记录无法解析") from None
            if not read_more():
                raise ValueError(f"{label}: 文件在记录中途结束") from None
            continue
        position = end
        yield value


# --------------------------------------------------------------------------
# 写
# --------------------------------------------------------------------------
def ensure_parent(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path: str | Path, payload: Any, *, indent: int | None = 4) -> Path:
    """原子写入 JSON：临时文件 + fsync + ``os.replace``，不会留下半截文件。"""
    path = ensure_parent(path)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_jsonl_atomic(path: str | Path, records: Iterable[dict]) -> int:
    """原子写入 JSONL，返回写出的记录数。"""
    path = ensure_parent(path)
    temporary = path.with_name(f"{path.name}.tmp")
    count = 0
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, **_JSONL_KWARGS) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return count


class JsonlWriter:
    """异步安全的追加式 JSONL 写入器。

    100 路并发的 LLM 调用会在同一时刻回写结果，因此写盘必须串行化；
    实际 IO 放到线程里执行，避免阻塞事件循环。每条记录写后立即 flush，
    进程被中断时最多丢失正在写的那一行。
    """

    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = ensure_parent(path)
        self.fsync = fsync
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._handle = None

    def _write_line(self, line: str) -> None:
        with self._thread_lock:
            if self._handle is None:
                self._handle = self.path.open("a", encoding="utf-8")
            self._handle.write(line)
            self._handle.flush()
            if self.fsync:
                os.fsync(self._handle.fileno())

    async def append(self, record: dict) -> None:
        line = json.dumps(record, **_JSONL_KWARGS) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write_line, line)

    def close(self) -> None:
        with self._thread_lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


_WRITERS: dict[str, JsonlWriter] = {}


def get_writer(path: str | Path, *, fsync: bool = False) -> JsonlWriter:
    """按路径复用写入器，保证同一文件只有一个打开句柄。"""
    key = str(Path(path).resolve())
    writer = _WRITERS.get(key)
    if writer is None:
        writer = _WRITERS[key] = JsonlWriter(path, fsync=fsync)
    return writer


def close_all() -> None:
    for writer in _WRITERS.values():
        writer.close()
    _WRITERS.clear()


# --------------------------------------------------------------------------
# 运行标记
# --------------------------------------------------------------------------
RUN_MARKER_SUFFIX = ".run.json"


def run_marker_path(output: str | Path) -> Path:
    """旁挂运行标记的路径：在输出文件旁追加 ``.run.json``。

    运行指纹是整次运行的常量。逐行重复既撑大结果文件，又让每条记录都背上与
    自己无关的配置信息，因此单独存一份：结果文件只保留逐实例的数据。
    """
    output = Path(output)
    return output.with_name(output.name + RUN_MARKER_SUFFIX)


def read_run_fingerprint(output: str | Path) -> str | None:
    """读标记里的指纹；标记不存在或没有指纹时返回 ``None``。"""
    path = run_marker_path(output)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    fingerprint = payload.get("fingerprint") if isinstance(payload, Mapping) else None
    return str(fingerprint) if fingerprint else None


def write_run_fingerprint(output: str | Path, fingerprint: str) -> Path:
    """把这条输出路径认领给当前配置；同一次运行重复写是幂等的。"""
    return write_json_atomic(run_marker_path(output), {
        "fingerprint": fingerprint,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })


def check_or_claim_run_fingerprint(
        output: str | Path,
        fingerprint: str,
        *,
        has_records: bool,
        check_fingerprint: bool = True,
        claim: bool = True,
        mismatch_message: str,
) -> None:
    """校验已有结果的标记，或在空输出上按需认领路径。

    关闭校验时完全不读取标记；仅当输出为空且标记不存在时才允许新建标记，
    从而保留已有标记的原始字节，也不把已有的无标记结果错误地认领。
    """
    if has_records:
        if check_fingerprint and read_run_fingerprint(output) != fingerprint:
            raise ValueError(mismatch_message)
        return

    if claim:
        marker = run_marker_path(output)
        # 严格模式下空输出会重新写入标记，从而覆盖损坏的标记。
        # 绕过模式不能改动已经存在的标记，哪怕输出目前为空。
        if check_fingerprint or not marker.exists():
            write_run_fingerprint(output, fingerprint)


# --------------------------------------------------------------------------
# 迁移
# --------------------------------------------------------------------------
def migrate_json_to_jsonl(source: str | Path, destination: str | Path, *,
                          expect_count: int | None = None,
                          overwrite: bool = False) -> int:
    """把「记录数组」形式的 JSON 迁移成 JSONL。

    流式解析源文件，因此数百 MB 的提取结果也不会把内存吃满。写出前校验条数，
    数量不符则删除临时文件并报错，避免半成品覆盖好数据。
    """
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise FileNotFoundError(f"找不到源文件: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("源路径与目标路径不能相同")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在，需显式传 overwrite: {destination}")

    ensure_parent(destination)
    temporary = destination.with_name(f"{destination.name}.tmp")
    count = 0
    try:
        with source.open("r", encoding="utf-8") as src, temporary.open("w", encoding="utf-8") as dst:
            for record in _iter_json_array_stream(src, label=str(source)):
                dst.write(json.dumps(record, **_JSONL_KWARGS) + "\n")
                count += 1
            dst.flush()
            os.fsync(dst.fileno())
        if expect_count is not None and count != expect_count:
            raise ValueError(f"{source} 迁移出 {count} 条，与期望的 {expect_count} 条不符")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return count


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m recsub.io",
        description="JSONL 工具：迁移记录数组形式的 JSON、校验条数",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="把记录数组形式的 JSON 转成 JSONL")
    migrate.add_argument("--from", dest="source", type=Path, required=True, help="源 JSON 文件")
    migrate.add_argument("--to", dest="destination", type=Path, required=True, help="目标 JSONL 文件")
    migrate.add_argument("--expect-count", type=int, default=None, help="期望条数，不符则报错且不落盘")
    migrate.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的目标文件")

    count = subparsers.add_parser("count", help="统计 JSONL 非空行数")
    count.add_argument("paths", nargs="+", type=Path)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "migrate":
        try:
            total = migrate_json_to_jsonl(
                args.source, args.destination,
                expect_count=args.expect_count, overwrite=args.overwrite,
            )
        except Exception as error:
            print(f"❌ 迁移失败：{error}", file=sys.stderr)
            return 1
        print(f"✅ {args.source} → {args.destination}：{total} 条")
        return 0

    failed = False
    for path in args.paths:
        if not path.is_file():
            print(f"❌ 找不到文件：{path}", file=sys.stderr)
            failed = True
            continue
        print(f"{path}: {count_jsonl(path)} 条")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
