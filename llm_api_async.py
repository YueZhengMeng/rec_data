import asyncio
import os

from config import CONFIG, LLM_CONCURRENCY_SEM


async def openai_complete_async(messages, client, model_name, stream=False):
    # 重试次数
    retries = 0
    while retries < CONFIG["llm_max_retries"]:
        try:
            # 创建空字符串，用于保存模型的回复
            content = ""
            reasoning_content = ""
            # 有些模型没有返回的usage，所以需要占位符
            input_tokens = None
            output_tokens = None

            # 限制并发数
            async with LLM_CONCURRENCY_SEM:
                if stream:
                    # 流式调用
                    completion_stream = await client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        extra_body={"enable_thinking": True},
                        stream=True,
                    )
                    async for event in completion_stream:
                        if event.choices and event.choices[0].delta:
                            delta = event.choices[0].delta
                            # 如果本次的新回复是reasoning_content，则是思考的内容
                            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                reasoning_content += delta.reasoning_content
                            # 如果本次的新回复是content，则是模型的回复
                            if delta.content:
                                content += delta.content

                        # usage 可能在最后一个事件里返回
                        # 保险起见，实时获取并更新
                        if hasattr(event, "usage") and event.usage:
                            input_tokens = getattr(event.usage, "prompt_tokens", None)
                            output_tokens = getattr(event.usage, "completion_tokens", None)

                else:
                    completion = await client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        extra_body={"enable_thinking": True},
                    )
                    message = completion.choices[0].message
                    content = message.content or ""
                    model_extra = getattr(message, "model_extra", {}) or {}
                    reasoning_content = model_extra.get("reasoning_content") or ""
                    usage = getattr(completion, "usage", None)
                    input_tokens = getattr(usage, "prompt_tokens", None)
                    output_tokens = getattr(usage, "completion_tokens", None)

            # 将结果保存在messages中
            if reasoning_content and len(reasoning_content) > 0:
                # think模型的多轮对话，不能将reasoning_content放入content中
                # 这里放进think字段中，只是备份，不会对模型的生成过程产生影响
                # 同时记录token消耗量
                messages.append({"role": "assistant", "content": content,
                                 "think": reasoning_content,
                                 "input_tokens": input_tokens,
                                 "output_tokens": output_tokens})
            else:
                messages.append({"role": "assistant", "content": content,
                                 "input_tokens": input_tokens,
                                 "output_tokens": output_tokens})
            # 返回结果
            return messages

        except Exception as e:
            print(f"Attempt {retries + 1} failed: {e}")
            retries += 1
            if retries >= CONFIG["llm_max_retries"]:
                # 达到最大重试次数后，抛出异常
                # 这种情况下，肯定是网络出现了严重故障，实验无法继续进行
                # raise Exception(f"请求失败，已达到最大重试次数")

                # 这种情况下，返回None，跳过当前数据
                # 主实验有断点继续功能，重新运行可以只处理剩余数据
                return None

    return None


if __name__ == "__main__":
    from openai import AsyncOpenAI

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "谁赢得了2020年的世界棒球大赛？"},
        {"role": "assistant", "content": "洛杉矶道奇队在2020年赢得了世界棒球大赛。",
         "think": "我喜欢吃苹果"},
        {"role": "user",
         "content": "这是一条测试消息：关于我喜欢吃的东西。我在前文中给出了答案，请问你是否有看到。如果看到，请直接告诉我答案。"}
    ]
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name = "qwen3.8-max"
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    messages = asyncio.run(openai_complete_async(messages, client, model_name))
    content = messages[-1]["content"]
    # 因为拥有think能力的模型的think部分不能放进多轮对话历史记录，且tokenizer的chat template部分不会拼接加入think字段
    # 所以这里的预期回答应该是“否”
    print(content)
