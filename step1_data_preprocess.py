import json


def main():
    # 加载原始注释
    with open("./anno/annotations.json", "r", encoding="utf-8") as f:
        anno = json.load(f)

    # instance_id 是唯一主键
    instance_list = []
    instance_id = 0
    image_idx = 0
    for image_name, image_anno in anno.items():
        caption_idx = 0
        for caption, caption_anno in image_anno.items():
            point_idx = 0
            for point in caption_anno["points"]:
                instance_list.append({
                    "image_name": image_name,
                    "caption": caption,
                    "category": caption_anno["class"],
                    "attributes": caption_anno["attribute"],
                    "point": point,
                    "type": caption_anno["type"],
                    "instance_id": instance_id,
                    "image_idx": image_idx,
                    "caption_idx": caption_idx,
                    "point_idx": point_idx,
                })
                instance_id += 1
                point_idx += 1
            caption_idx += 1
        image_idx += 1

    with open("./anno/instance_data.json", "w", encoding="utf-8") as f:
        json.dump(instance_list, f, ensure_ascii=False, indent=4)

    print("总数据量：", len(instance_list))


if __name__ == "__main__":
    main()
