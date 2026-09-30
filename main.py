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
    allowed_keys = {"appearance", "action", "location", "relation"}
    attributes_extract_result = match_json(attributes_extractor_content)

    if (not isinstance(attributes_extract_result, dict)
            or not allowed_keys.intersection(attributes_extract_result)
            or any(key not in allowed_keys for key in attributes_extract_result)
            or any(not isinstance(value, list)
                   or any(not isinstance(item, str) for item in value)
                   for value in attributes_extract_result.values())):
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

    id_list = [507, 1557, 2265, 2332, 2353, 2397, 2399, 2452, 3271, 4383, 4388, 6207, 6212, 9212, 10070, 10072, 10336,
               10351, 10384, 10987, 11971, 12185, 12394, 12699, 13720, 14206, 14238, 16446, 16503, 17173, 17186, 17213,
               17219, 17220, 18252, 18379, 19167, 19349, 19422, 19584, 20033, 20431, 20494, 20665, 20677, 20678, 20684,
               20699, 21033, 21044, 21671, 21703, 21936, 22432, 22648, 22683, 22713, 22764, 22976, 22979, 23003, 23066,
               23067, 23250, 23300, 23353, 23576, 24187, 24214, 24233, 24239, 25808, 27160, 27174, 27429, 27457, 27506,
               27540, 27544, 27864, 27899, 27975, 27982, 28004, 28310, 29289, 29359, 29572, 29664, 29818, 30511, 30553,
               31667, 32069, 32201, 32286, 32293, 32315, 32436, 32716, 33076, 33329, 33452, 33817, 33835, 34162, 34424,
               34595, 34689, 35164, 35329, 36006, 36040, 36406, 37678, 37940, 38178, 40639, 45406, 45638, 46114, 47868,
               54827, 57507, 59325, 59367, 60482, 62447, 62499, 63667, 64370, 66274, 66282, 69319, 69832, 70480, 73736,
               75583, 79098, 105019, 118061, 123275, 129106, 129260, 130290, 137601, 138236, 139961, 140205, 140256,
               145063, 153230, 154914, 177327, 183066, 185427, 188256, 191173, 192014, 193556, 193831, 196373, 201133,
               201706, 203126, 205142, 205450, 206107, 206149, 206880, 208329, 208436, 208782, 209155, 209484, 209983,
               211563, 212077, 212916, 212976, 213174, 213374, 219343, 224656, 225077, 229052, 235498, 236118, 237401,
               245408, 254818, 258203, 264820, 265469, 267211, 267397, 267531, 267626, 267969, 269020, 269375, 269405,
               270537, 271233, 271242, 271326, 271331, 271764, 271856, 272200, 273606, 273825, 274241, 274247, 274595,
               274889, 275126, 275369, 276239, 276331, 276614, 276907, 277127, 278234, 278967, 279025, 279876]
    instance_all = [item for item in instance_all if item["instance_id"] in id_list]

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
