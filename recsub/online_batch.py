"""在线批次的逐条完成进度；handler 返回前应完成该条结果的写入。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Mapping, Sequence

from tqdm import tqdm


async def run_online_batch(
        items: Sequence[Mapping[str, Any]],
        handle: Callable[[Mapping[str, Any]], Awaitable[None]],
        *, desc: str,
) -> None:
    """并发执行一批任务；少量慢任务未完成时显示其 instance_id。"""
    pending = {index: item["instance_id"] for index, item in enumerate(items)}

    async def one(index: int, item: Mapping[str, Any]) -> int:
        await handle(item)
        return index

    with tqdm(total=len(items), desc=desc, unit="条", dynamic_ncols=True) as progress:
        def show_pending() -> None:
            status: dict[str, int | str] = {"remaining": len(pending)}
            if 0 < len(pending) <= 5:
                status["pending_ids"] = ",".join(str(value) for value in pending.values())
            progress.set_postfix(status, refresh=False)

        show_pending()
        progress.refresh()
        tasks = [asyncio.create_task(one(index, item)) for index, item in enumerate(items)]
        try:
            for completed in asyncio.as_completed(tasks):
                index = await completed
                pending.pop(index)
                show_pending()
                progress.update(1)
                if len(pending) <= 5:
                    progress.refresh()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
