import json

with open("./exp/exp_0909/result.json", "r", encoding="utf-8") as f:
    data = json.load(f)

total_input_tokens = 0
total_output_tokens = 0
for d in data:
    total_input_tokens += d["input_tokens"]
    total_output_tokens += d["output_tokens"]

print(f"总输入token数: {total_input_tokens}, 总输出token数: {total_output_tokens}")
print(f"平均输入token数: {total_input_tokens / len(data)}, 平均输出token数: {total_output_tokens / len(data)}")

"""
We recommend using the following set of sampling parameters for generation

Non-thinking mode for text tasks: temperature=1.0, top_p=1.00, top_k=20, min_p=0.0, presence_penalty=2.0, repetition_penalty=1.0
Non-thinking mode for VL tasks: temperature=0.7, top_p=0.80, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0
Thinking mode for text tasks: temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0
Thinking mode for VL or precise coding (e.g. WebDev) tasks : temperature=0.6, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0, repetition_penalty=1.0
Please note that the support for sampling parameters varies according to inference frameworks.
"""
