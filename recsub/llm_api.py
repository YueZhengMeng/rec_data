"""在线多模态调用：客户端、并发闸门、重试，以及三视图裁剪。

:class:`OnlineModel` 把端点、并发闸门与重试策略绑在一起，并且必须在事件循环内
创建——并发信号量随实例创建，不在导入时构造 asyncio 原语。调用方拿到的是结构化
的 :class:`Completion`，而不是整段 messages，避免把 base64 图片写进结果文件。

模型名、端点、并发数、重试次数、超时与 ``enable_thinking`` 的默认值都来自
``recsub/config.py`` 的 ``online`` 段，判断步骤可用 ``--no-enable-thinking`` 覆盖。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from openai import AsyncOpenAI

from recsub.config import CONFIG, crop_config
from recsub.crop import AdaptiveCropper, CropConfig, CropMetadata
from recsub.utils import encode_image


@dataclass
class Completion:
    """一次成功的在线回复。"""

    content: str
    reasoning: str
    input_tokens: int | None
    output_tokens: int | None
    attempts: int

    def usage(self) -> dict[str, int | None]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class OnlineModel:
    """一个模型端点 + 并发闸门 + 重试策略。必须在事件循环内创建。"""

    client: AsyncOpenAI
    model_name: str
    semaphore: asyncio.Semaphore
    max_retries: int
    enable_thinking: bool

    @classmethod
    def create(
            cls,
            *,
            concurrency: int | None = None,
            max_retries: int | None = None,
            enable_thinking: bool | None = None,
    ) -> "OnlineModel":
        settings = CONFIG["online"]
        api_key = CONFIG.get("api_key")
        if not api_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY，无法调用在线模型")
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=CONFIG["base_url"],
        ).with_options(timeout=settings["timeout"])
        return cls(
            client=client,
            model_name=CONFIG["model_name"],
            semaphore=asyncio.Semaphore(concurrency or settings["concurrency"]),
            max_retries=max_retries if max_retries is not None else settings["max_retries"],
            enable_thinking=(settings["enable_thinking"]
                             if enable_thinking is None else enable_thinking),
        )

    async def complete(self, messages: Sequence[Mapping[str, Any]], *,
                       stream: bool = False) -> Completion | None:
        """调用一次；重试耗尽后返回 ``None``（调用方据此区分失败与内容为空）。"""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                content, reasoning, input_tokens, output_tokens = await self._once(messages, stream)
            except Exception as error:  # 网络/限流/服务端错误都走重试
                last_error = error
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 30))
                continue

            if not content.strip():
                # 空回复多半是瞬时抖动，也按失败重试；否则解析阶段会把它当成格式错误。
                last_error = ValueError("模型返回空内容")
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 30))
                continue
            return Completion(content, reasoning, input_tokens, output_tokens, attempt)

        if last_error is not None:
            print(f"⚠️ 调用失败（已重试 {self.max_retries} 次）：{last_error}")
        return None

    async def _once(self, messages: Sequence[Mapping[str, Any]], stream: bool):
        async with self.semaphore:
            if stream:
                completion = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=list(messages),
                    extra_body={"enable_thinking": self.enable_thinking},
                    stream=True,
                )
                content = ""
                reasoning = ""
                input_tokens = output_tokens = None
                async for event in completion:
                    if event.choices and event.choices[0].delta:
                        delta = event.choices[0].delta
                        reasoning_delta = getattr(delta, "reasoning_content", None)
                        if reasoning_delta:
                            reasoning += reasoning_delta
                        if delta.content:
                            content += delta.content
                    usage = getattr(event, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "prompt_tokens", None)
                        output_tokens = getattr(usage, "completion_tokens", None)
                return content, reasoning, input_tokens, output_tokens

            completion = await self.client.chat.completions.create(
                model=self.model_name,
                messages=list(messages),
                extra_body={"enable_thinking": self.enable_thinking},
            )
            message = completion.choices[0].message
            reasoning = (getattr(message, "model_extra", {}) or {}).get("reasoning_content") or ""
            usage = getattr(completion, "usage", None)
            return (message.content or "", reasoning,
                    getattr(usage, "prompt_tokens", None),
                    getattr(usage, "completion_tokens", None))


# ============================================================
# 三视图
# ============================================================
def load_three_views(
        instance: Mapping[str, Any],
        image_dir: str | os.PathLike,
        cropper: AdaptiveCropper,
) -> tuple[str, str, str, CropMetadata]:
    """按实例的图、类别、点裁出三视图并编码成 base64。

    这是同步的 PIL 工作，调用方应当放进线程（``asyncio.to_thread``），
    否则 100 路并发下会把事件循环堵住。
    """
    from PIL import Image

    image_path = os.path.join(str(image_dir), instance["image_name"])
    with Image.open(image_path) as source:
        crops = cropper.create_crops(
            source.convert("RGB"),
            image_name=instance["image_name"],
            category=instance["category"],
            point=instance["point"],
        )
    images = tuple(encode_image(image) for image in (
        crops.full_image, crops.context_image, crops.fine_image,
    ))
    return images[0], images[1], images[2], crops.metadata


def build_cropper(instances) -> AdaptiveCropper:
    """用完整实例表建立密度索引，裁剪参数取自 ``CONFIG["crop"]``。"""
    return AdaptiveCropper.from_instances(instances, crop_config())


__all__ = [
    "Completion", "OnlineModel", "load_three_views", "build_cropper",
    "CropConfig", "CropMetadata", "AdaptiveCropper",
]
