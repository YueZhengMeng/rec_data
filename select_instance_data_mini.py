import json
from collections import defaultdict

# 读取 ./anno/instance_data.jsonl
with open('./anno/instance_data.jsonl', 'r', encoding='utf-8') as f:
    data = [json.loads(line) for line in f]

# (类别, 数据集) -> 需要选取的图片数，按图片在文件中首次出现的顺序取前 N 张
QUOTA = {
    ('person', 'cs'): 10,
    ('person', 'mall'): 6,
    ('person', 'nwpu'): 1,
    ('person', 'visdrone'): 1,
    ('person', 'internet'): 1,
    ('car', 'detrac'): 5,
    ('car', 'carpk'): 1,
}

# 总集前 10 张图片（按首次出现顺序），保留其全部实例
first_images = list(dict.fromkeys(item['image_name'] for item in data))[:10]

# 数据集来源是图片名中第一段数字后的 token，如 0000-fsc147-1106.jpg -> fsc147
selected = defaultdict(list)
for item in data:
    key = (item['category'], item['image_name'].split('-')[1])
    if key not in QUOTA:
        continue
    names = selected[key]
    if item['image_name'] not in names and len(names) < QUOTA[key]:
        names.append(item['image_name'])

# 按原文件顺序保留选中图片（前 10 张 + 各类别配额）的全部实例
chosen = set(first_images)
for names in selected.values():
    chosen.update(names)
data = [item for item in data if item['image_name'] in chosen]

with open('./anno/instance_data_mini.jsonl', 'w', encoding='utf-8') as f:
    for item in data:
        f.write(json.dumps(item, ensure_ascii=False) + '\n')

# 统计输出
first_set = set(first_images)
first_inst = sum(1 for item in data if item['image_name'] in first_set)
overlap = len(first_set) + sum(len(v) for v in selected.values()) - len(chosen)
print(f'first 10 images (whole set): {first_inst} instances')
print(f'overlap with quota images: {overlap}')
print(f'total: {len(data)} instances, {len(chosen)} images')
for key in QUOTA:
    key_inst = sum(1 for item in data
                   if (item['category'], item['image_name'].split('-')[1]) == key)
    print(f'{key[0]:6s} {key[1]:9s}: {len(selected[key])} images, {key_inst} instances')
