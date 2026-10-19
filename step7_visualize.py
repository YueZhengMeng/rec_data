"""step7：可视化 step6 选出的细粒度正向子集。

每张 PNG 展示一个子集：红叉标记正样本，绿点标记同根 caption 其余实例。
互补节点不单独生成图片。渲染器由
:mod:`recsub.subset_visualization` 提供，step8 审核界面复用同一套布局。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.subset_visualization import load_subset_nodes, render_subset


def render_page(image: Image.Image, image_name: str, caption: str,
                node: dict, *, subset_index: int = 1, page: int | None = None,
                max_width: int = 1100, max_height: int = 850,
                sidebar_width: int = 520) -> Image.Image:
    """Backward-compatible name for the shared one-subset renderer."""
    return render_subset(image, node, subset_index=page or subset_index, mode="positive",
                         max_width=max_width, max_height=max_height,
                         sidebar_width=sidebar_width)


def run(args: argparse.Namespace) -> int:
    nodes = load_subset_nodes(args.annotations, positive_only=True)
    if args.image_names:
        allowed = {name.strip() for name in args.image_names.split(",") if name.strip()}
        nodes = [node for node in nodes if node["image_name"] in allowed]
    if not nodes:
        print("没有可视化的 step6 正向子集")
        return 0
    if min(args.max_width, args.max_height, args.sidebar_width) <= 0:
        raise ValueError("max-width、max-height 和 sidebar-width 必须大于 0")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    grouped_index: dict[tuple[str, str], int] = {}
    for node in sorted(nodes, key=lambda item: (item["image_name"], item["caption"], item["node_id"])):
        image_name, caption = str(node["image_name"]), str(node["caption"])
        image_path = args.image_dir / image_name
        if not image_path.is_file():
            print(f"⚠️ 找不到图片，跳过：{image_path}", file=sys.stderr)
            continue
        key = (image_name, caption)
        grouped_index[key] = grouped_index.get(key, 0) + 1
        with Image.open(image_path) as source:
            figure = render_subset(source, node, subset_index=grouped_index[key], mode="positive",
                                   max_width=args.max_width, max_height=args.max_height,
                                   sidebar_width=args.sidebar_width)
        destination = args.output_dir / f"{Path(image_name).stem}__{node['node_id']}.png"
        figure.save(destination, format="PNG", optimize=True)
        written += 1
    print(f"✅ 生成 {written} 张 step7 子集图 → {args.output_dir}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染 step6 细粒度正向子集（每张图一个子集）")
    parser.add_argument("--annotations", type=Path, default=path("subsets"),
                        help="step6 子集节点 JSONL")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--output-dir", type=Path, default=path("output_dir") / "positive")
    parser.add_argument("--image-names", default=None, help="只渲染逗号分隔图片")
    parser.add_argument("--max-width", type=int, default=CONFIG["visualize"]["max_width"])
    parser.add_argument("--max-height", type=int, default=CONFIG["visualize"]["max_height"])
    parser.add_argument("--sidebar-width", type=int, default=CONFIG["visualize"]["sidebar_width"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if not args.annotations.is_file():
        print(f"❌ 找不到 annotations：{args.annotations}", file=sys.stderr)
        return 1
    try:
        return run(args)
    except Exception as error:
        print(f"❌ 可视化失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
