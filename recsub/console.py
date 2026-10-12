"""控制台输出编码。

Windows 默认代码页是 GBK，而各步骤会打印 ✅ / ⚠️ 等符号，直接输出会抛
``UnicodeEncodeError``。每个步骤脚本在 ``main()`` 开头调用一次即可。
"""

from __future__ import annotations

import sys


def configure_console() -> None:
    """把标准输出/错误切到 UTF-8；``reconfigure`` 不可用时静默跳过。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # 已被重定向到不支持切换编码的对象时忽略，不因此中断流程。
            pass
