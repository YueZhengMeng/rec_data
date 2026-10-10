import json
import os.path

from PIL import Image
from openai import AsyncOpenAI

from crop import AdaptiveCropper
from llm_api_async import openai_complete_async
from prompts import (
    EXTRACT_ATTRIBUTES_PROMPT,
    PREDICATE_JUDGMENT_PROMPT,
    build_extract_attributes_messages,
    build_extract_attributes_prompt,
    build_instance_messages,
)
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


class VisualAgent(BaseAgent):
    """Shared online three-view inference for extraction and predicate judging."""

    def __init__(self, agent_name, api_key, base_url, model_name, image_dir, cropper: AdaptiveCropper):
        super().__init__(agent_name, api_key, base_url, model_name)
        self.image_dir = image_dir
        self.cropper = cropper

    async def infer(self, instance_data, *, text_prompt):
        image_path = os.path.join(self.image_dir, instance_data["image_name"])
        with Image.open(image_path) as source_image:
            crops = self.cropper.create_crops(
                source_image.convert("RGB"),
                image_name=instance_data["image_name"],
                category=instance_data["category"],
                point=instance_data["point"],
            )
            images = tuple(encode_image(image) for image in (
                crops.full_image, crops.context_image, crops.fine_image
            ))
        messages = build_instance_messages(*images, text_prompt)
        reply = await openai_complete_async(messages, self.client, self.model_name)
        if not reply or not isinstance(reply, list) or not isinstance(reply[-1], dict):
            return None, None
        content = reply[-1].get("content")
        return (content if isinstance(content, str) else None), reply


class AttributesExtractor(VisualAgent):
    def __init__(self, agent_name, api_key, base_url, model_name, image_dir, cropper: AdaptiveCropper):
        super().__init__(agent_name, api_key, base_url, model_name, image_dir, cropper)
        self.prompt["extract_attributes"] = EXTRACT_ATTRIBUTES_PROMPT

    async def extract_attributes(self, instance_data):
        prompt = build_extract_attributes_prompt(
            instance_data["category"], instance_data["attributes"],
            self.prompt["extract_attributes"]
        )
        return await self.infer(instance_data, text_prompt=prompt)


class PredicateJudge(VisualAgent):
    """Classify closed visual predicates using the same three marked/cropped views."""

    async def judge(self, instance_data, phrases):
        text_prompt = (PREDICATE_JUDGMENT_PROMPT
                       + "\nOriginal caption: " + instance_data["caption"]
                       + "\nCategory: " + instance_data["category"]
                       + "\nAttribute phrases (JSON): " + json.dumps(phrases, ensure_ascii=False))
        content, messages = await self.infer(instance_data, text_prompt=text_prompt)
        return content, messages[-1] if messages else None
