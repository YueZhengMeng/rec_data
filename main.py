import asyncio
import json

from agents import AttributesExtractor
from config import CONFIG, EXP_RESULT_FILE, EXP_RESULT_FILE_LOCK
from crop import AdaptiveCropper, CropConfig
from utils import match_json

attributes_extractor = None


async def extract_attributes(instance_data):
    instance_id = instance_data["instance_id"]

    print(f"⏳ 数据 {instance_id} 开始提取")

    try:
        attributes_extractor_content, attributes_extractor_messages = \
            await attributes_extractor.extract_attributes(instance_data)
    except Exception as e:
        print(f"❌ 数据 {instance_id} 生成失败: {e}")
        return instance_id, False

    # 生成失败，返回
    if attributes_extractor_content is None or attributes_extractor_messages is None:
        print(f"❌ 数据 {instance_id} 生成失败，放弃")
        return instance_id, False

    # 解析结果
    required_keys = ["appearance", "action", "location", "relation"]
    attributes_extract_result = match_json(attributes_extractor_content, required_keys)

    if (not isinstance(attributes_extract_result, dict)
            or any(not isinstance(attributes_extract_result.get(key), list) for key in required_keys)):
        print(f"❌ 数据 {instance_id} 解析失败，放弃")
        return instance_id, False

    # 保存实验结果
    # 注意加互斥锁，避免多个协程同时写入
    async with EXP_RESULT_FILE_LOCK:
        # 加载现有的属性提取结果数据
        with open(EXP_RESULT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        # 保存解析后的属性和原始回复，避免把包含图片 base64 的请求消息重复写入结果文件
        existing_data.append({
            "instance_id": instance_id,
            "attributes": attributes_extract_result,
            "reasoning_content": attributes_extractor_messages[-1]["think"],
            "input_tokens": attributes_extractor_messages[-1]["input_tokens"],
            "output_tokens": attributes_extractor_messages[-1]["output_tokens"]
        })
        # 保存更新后的数据
        with open(EXP_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=4)

    print(f"✅ 数据 {instance_id} 提取成功")
    return instance_id, True


async def batched_process(instance_list):
    # 划分数据集为多个batch，避免一次性创建太多的协程
    batch_size = CONFIG["process_batch_size"]
    results = []
    for i in range(0, len(instance_list), batch_size):
        batch = instance_list[i:i + batch_size]
        # 创建协程任务
        tasks = []
        for item in batch:
            tasks.append(extract_attributes(item))
        # 并发执行；单条任务失败不应中断整个 batch
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        results.extend(batch_results)
    return results


if __name__ == "__main__":
    # 图片文件夹
    image_dir = CONFIG["image_dir"]

    # 加载实例数据
    with open("./anno/instance_data.json", "r", encoding="utf-8") as f:
        instance_all = json.load(f)

    # 必须使用完整数据建立同图同类别的点密度索引，再限制本次调用数量
    crop_config = CropConfig(**CONFIG["crop"])
    cropper = AdaptiveCropper.from_instances(instance_all, crop_config)
    print(
        f"✅ Crop索引构建完成：{cropper.group_count}组，"
        f"{cropper.unique_point_count}个唯一点，"
        f"忽略{cropper.duplicate_point_count}个重复点"
    )

    # 随机抽取100条数据
    # instance_all = random.sample(instance_all, 100)

    instance_all = instance_all[:10]

    # 加载已有的实验结果
    with open(EXP_RESULT_FILE, "r", encoding="utf-8") as f:
        existing_result = json.load(f)

    # 获取已完成属性提取的数据id；兼容早期使用的 id 字段
    extracted_id_set = {item["instance_id"] for item in existing_result}

    # 过滤，只保留未处理的数据
    instance_list = [
        item for item in instance_all
        if item["instance_id"] not in extracted_id_set
    ]

    print(f"✅ {len(instance_all) - len(instance_list)} 条数据已完成属性提取")
    print(f"⏳ {len(instance_list)} 条数据待提取")

    # 创建智能体
    attributes_extractor = AttributesExtractor(
        "attributes_extractor",
        CONFIG["api_key"],
        CONFIG["base_url"],
        CONFIG["model_name"],
        image_dir,
        cropper,
    )

    # 启动属性提取
    final_results = asyncio.run(batched_process(instance_list))

    # 输出汇总
    success_count = 0
    for instance_id, success in final_results:
        if success:
            success_count += 1
    print(f"总成功数: {success_count}/{len(instance_list)}")
