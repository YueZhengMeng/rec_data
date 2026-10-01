import json

import unicodedata


def normalize_phrase(text):
    """统一大小写、Unicode 兼容字符、标点和多余空白。"""
    # NFKC 把全角、兼容字符、连字统一
    # casefold 做比 lower 更彻底的大小写折叠
    text = unicodedata.normalize("NFKC", text).casefold()
    # 逐字符遍历，取每个字符的 Unicode General Category
    # 以 P 开头的类别都是标点
    text = "".join(
        " " if unicodedata.category(char).startswith("P") else char
        for char in text
    )
    return " ".join(text.split())


def normalize_and_deduplicate(values):
    """归一化属性短语，并按归一化文本稳定去重。"""
    normalized_values = []
    seen = set()
    for value in values:
        normalized = normalize_phrase(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_values.append(normalized)
        else:
            # 空字符串和归一化后的重复短语直接丢弃，不影响首次出现项。
            continue
    return normalized_values


def main():
    from tqdm import tqdm

    from main_local import parse_attributes
    from utils import extract_and_parse_json

    with open("./exp/exp_local_qwen35_2B/result.json", "r", encoding="utf-8") as f:
        data_student = json.load(f)

    # with open("./exp/exp_local_qwen35_4B/result.json", "r", encoding="utf-8") as f:
    #     data_student = json.load(f)

    fail_list = []

    for i in tqdm(range(len(data_student))):
        attributes = extract_and_parse_json(data_student[i]["content"])
        if attributes:
            # attributes 转换回 json 字符串
            attributes_text = json.dumps(attributes)

            # 手动修一部分
            if type(attributes) is dict and "location" in attributes.keys():
                if type(attributes["location"]) is str:
                    attributes_text = attributes_text.replace('location": "', 'location": ["')
                    attributes_text = attributes_text.replace('", "relation"', '"], "relation"')

            # 手动修一部分
            if type(attributes) is dict and "relation" in attributes.keys():
                if type(attributes["relation"]) is str:
                    attributes_text = attributes_text.replace('relation": "', 'relation": ["')
                    attributes_text = attributes_text.replace('"}', '"]}')

            # 再过一遍格式检查
            attributes, parse_error, _, _ = parse_attributes(attributes_text)
            if attributes:
                data_student[i]["attributes"] = attributes
            else:
                print(f"Error processing item {attributes_text}: {parse_error}")
                fail_list.append(data_student[i])
        else:
            fail_list.append(data_student[i])

    # fail_id_list 会被送去由教师模型标注
    fail_id_list = [item["instance_id"] for item in fail_list]
    print(len(fail_id_list))
    print(fail_id_list)

    # 加载教师模型标注结果
    with open("./exp/exp_all/result.json", "r", encoding="utf-8") as f:
        data_teacher = json.load(f)

    teacher_count = 0
    for item in data_teacher:
        data_student[item["instance_id"]]["attributes"] = item["attributes"]
        teacher_count += 1
    print("Teacher Model Annotations Loaded:", teacher_count)

    for i in tqdm(range(len(data_student))):
        assert data_student[i]["attributes"] is not None
        # 预处理并去重后写回属性字典；之后 extract_result.json 中保存的
        # 就是统一形式的短语，不需要下游再重复做字符归一化。
        for field, values in data_student[i]["attributes"].items():
            data_student[i]["attributes"][field] = normalize_and_deduplicate(values)
        data_student[i] = {
            "instance_id": data_student[i]["instance_id"],
            "more_attributes": data_student[i]["attributes"],
        }

    extract_result = []

    with open("./anno/instance_data.json", "r", encoding="utf-8") as f:
        instance_data = json.load(f)

    for i in range(len(data_student)):
        assert instance_data[i]["instance_id"] == data_student[i]["instance_id"]
        extract_result.append({**instance_data[i], **data_student[i]})

    with open("./anno/extract_result.json", "w", encoding="utf-8") as f:
        json.dump(extract_result, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main()
