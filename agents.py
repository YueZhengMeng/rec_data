import os.path

from PIL import Image
from openai import AsyncOpenAI

from crop import AdaptiveCropper
from llm_api_async import openai_complete_async
from prompts import EXTRACT_ATTRIBUTES_PROMPT, build_extract_attributes_messages
from utils import encode_image


class BaseAgent:
    def __init__(self, agent_name, api_key, base_url, model_name):
        self.agent_name = agent_name
        self.model_name = model_name
        self.prompt = {}
        # 调用LLM的API客户端
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        ).with_options(timeout=1800.0)


class AttributesExtractor(BaseAgent):
    def __init__(self, agent_name, api_key, base_url, model_name, image_dir, cropper: AdaptiveCropper):
        super().__init__(agent_name, api_key, base_url, model_name)
        self.image_dir = image_dir
        self.cropper = cropper
        self.prompt["extract_attributes"] = EXTRACT_ATTRIBUTES_PROMPT

    async def extract_attributes(self, instance_data):
        # 读取图片并在内存中构造全局、上下文和细粒度视图
        image_path = os.path.join(self.image_dir, instance_data["image_name"])
        with Image.open(image_path) as source_image:
            image_global = source_image.convert("RGB")
            crops = self.cropper.create_crops(
                image_global,
                image_name=instance_data["image_name"],
                category=instance_data["category"],
                point=instance_data["point"],
            )
            base64_image_global = encode_image(crops.full_image)
            base64_image_context = encode_image(crops.context_image)
            base64_image_fine = encode_image(crops.fine_image)

        # 复用统一消息构造函数，保证在线请求和 Batch JSONL 的输入完全一致
        messages = build_extract_attributes_messages(
            base64_image_global,
            base64_image_context,
            base64_image_fine,
            instance_data["category"],
            instance_data["attributes"],
            self.prompt["extract_attributes"],
        )

        messages = await openai_complete_async(messages, self.client, self.model_name)
        if not messages or not isinstance(messages, list) or not isinstance(messages[-1], dict):
            return None, None
        content = messages[-1].get("content")
        if not isinstance(content, str):
            return None, messages
        return content, messages
