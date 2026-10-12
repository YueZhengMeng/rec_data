"""step8：渲染门禁后子集的人工复核图。

与旧的「每一层枚举数百个重叠子集」视图不同，这里按 image-caption 根渲染，并把
每个**正谓词**的三值分区画成三色点：

- 绿：模型判断 yes；红：no；灰：unknown；
- 侧栏给规范谓词、三值计数、来源与审核状态；
- complement 节点不重复画，旁栏说明它由正谓词生成；
- 每页最多 ``--max-predicates`` 个谓词，避免硬币图把一张 PNG 撑成几十万像素。

这只是教师伪标签的检查图，不是人工金标准。
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.io import iter_jsonl

PALETTE = (
    (0, 95, 115), (202, 103, 2), (155, 34, 38), (10, 147, 150),
    (106, 76, 147), (56, 102, 65), (42, 157, 143), (38, 70, 83),
    (143, 45, 86), (127, 85, 57), (29, 53, 87), (95, 15, 64),
)
YES = (15, 150, 90)
NO = (205, 55, 50)
UNKNOWN = (105, 105, 105)


def font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                pass
    return ImageFont.load_default()


def wrap(text: str, width: int) -> list[str]:
    words = str(text).split()
    result, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 <= width:
            current = word if not current else f"{current} {word}"
        else:
            if current:
                result.append(current)
            current = word
    if current:
        result.append(current)
    return result or [""]


def draw_point(draw: ImageDraw.ImageDraw, point: Sequence[float], scale: float,
               color: tuple[int, int, int], radius: int) -> None:
    x, y = float(point[0]) * scale, float(point[1]) * scale
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill=color, outline="white", width=max(1, radius // 3))


def render_page(image: Image.Image, image_name: str, caption: str,
                nodes: Sequence[Mapping[str, Any]], *, page: int,
                max_width: int = 1100, max_height: int = 850,
                sidebar_width: int = 520) -> Image.Image:
    source = image.convert("RGB")
    scale = min(max_width / source.width, max_height / source.height, 3.0)
    scale = max(scale, 1.0)
    displayed = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
    title_h, margin, gap = 62, 20, 18
    row_h = 72
    sidebar_h = margin + 80 + max(1, len(nodes)) * row_h + margin
    canvas_h = max(title_h + margin + displayed.height + margin, sidebar_h)
    canvas = Image.new("RGB", (margin + displayed.width + gap + sidebar_width + margin, canvas_h), (249, 249, 247))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 12), f"{Path(image_name).name} · {caption} · page {page}",
              fill=(10, 10, 10), font=font(22, True))
    draw.text((margin, 40), "green=yes · red=no · gray=unknown · generated pseudo-labels", fill=(80, 80, 80),
              font=font(13))
    image_x, image_y = margin, title_h
    canvas.paste(displayed, (image_x, image_y))
    marker_draw = ImageDraw.Draw(canvas)
    for node_index, node in enumerate(nodes):
        color = PALETTE[node_index % len(PALETTE)]
        # 同一谓词三种状态用颜色叠加的外圈/小点表达，先画 unknown 再 no 再 yes。
        for point in node.get("unknown_points", ()):
            draw_point(marker_draw, (image_x / scale + point[0], image_y / scale + point[1]), scale, UNKNOWN, 5)
        for point in node.get("no_points", ()):
            draw_point(marker_draw, (image_x / scale + point[0], image_y / scale + point[1]), scale, NO, 6)
        for point in node.get("yes_points", ()):
            draw_point(marker_draw, (image_x / scale + point[0], image_y / scale + point[1]), scale, YES, 7)

    sidebar_x = image_x + displayed.width + gap
    draw.rounded_rectangle((sidebar_x, title_h, sidebar_x + sidebar_width,
                            canvas_h - margin), radius=10, fill=(252, 252, 251), outline=(220, 220, 215), width=2)
    draw.text((sidebar_x + 16, title_h + 14), f"{len(nodes)} predicates", fill=(10, 10, 10), font=font(19, True))
    cursor = title_h + 52
    for node_index, node in enumerate(nodes, start=1):
        predicate = node.get("predicate") or {}
        counts = node.get("counts") or {}
        color = PALETTE[(node_index - 1) % len(PALETTE)]
        draw.ellipse((sidebar_x + 16, cursor + 4, sidebar_x + 29, cursor + 17), fill=color)
        text_x = sidebar_x + 38
        label = f"#{node_index} {predicate.get('display', predicate.get('predicate_id', '—'))}"
        draw.text((text_x, cursor), label, fill=(15, 15, 15), font=font(14, True))
        detail = (f"yes {counts.get('yes', 0)} · no {counts.get('no', 0)} · "
                  f"unknown {counts.get('unknown', 0)} · {predicate.get('source', '—')} · "
                  f"review {((node.get('review') or {}).get('status', 'pending'))}")
        draw.text((text_x, cursor + 22), detail, fill=(75, 75, 75), font=font(12))
        raw = ", ".join(predicate.get("raw_phrases") or ())
        draw.text((text_x, cursor + 40), "raw: " + (raw[:65] or "—"), fill=(120, 120, 120), font=font(11))
        cursor += row_h
    return canvas


def run(args: argparse.Namespace) -> int:
    point_by_id = {int(row["instance_id"]): row["point"] for row in iter_jsonl(args.instances)}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for node in iter_jsonl(args.annotations):
        if node.get("node_type") != "predicate_subset":
            continue
        if node.get("complement_of") or (node.get("predicate") or {}).get("polarity") == "negative":
            continue
        # step6 节点保存稳定 instance_id，不重复拷贝整张父集的坐标；可视化时回查实例表。
        node = dict(node)
        node["yes_points"] = [point_by_id[int(i)] for i in node.get("instance_ids", ()) if int(i) in point_by_id]
        node["no_points"] = [point_by_id[int(i)] for i in node.get("negative_instance_ids", ()) if
                             int(i) in point_by_id]
        node["unknown_points"] = [point_by_id[int(i)] for i in node.get("unknown_instance_ids", ()) if
                                  int(i) in point_by_id]
        key = (str(node["image_name"]), str(node["caption"]))
        groups[key].append(node)
    if args.image_names:
        allowed = {name.strip() for name in args.image_names.split(",") if name.strip()}
        groups = {key: value for key, value in groups.items() if key[0] in allowed}
    if not groups:
        print("没有可视化的正谓词节点")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for (image_name, caption), nodes in sorted(groups.items()):
        image_path = args.image_dir / image_name
        if not image_path.is_file():
            print(f"⚠️ 找不到图片，跳过：{image_path}", file=sys.stderr)
            continue
        with Image.open(image_path) as source:
            for page_start in range(0, len(nodes), args.max_predicates):
                page_nodes = nodes[page_start:page_start + args.max_predicates]
                figure = render_page(source, image_name, caption, page_nodes,
                                     page=page_start // args.max_predicates + 1,
                                     max_width=args.max_width, max_height=args.max_height,
                                     sidebar_width=args.sidebar_width)
                stem = Path(image_name).stem
                safe_caption = "_".join(caption.split())[:60]
                destination = args.output_dir / f"{stem}__{safe_caption}__p{page_start // args.max_predicates + 1}.png"
                figure.save(destination, format="PNG", optimize=True)
                written += 1
    print(f"✅ 生成 {written} 张审核图 → {args.output_dir}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染门禁后子集的三值检查图")
    parser.add_argument("--annotations", type=Path, default=path("subsets"),
                        help="step6 子集节点 JSONL")
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="step1 实例表，用于把 instance_id 映射回点坐标")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--output-dir", type=Path, default=path("output_dir"))
    parser.add_argument("--image-names", default=None, help="只渲染逗号分隔图片")
    parser.add_argument("--max-predicates", type=int, default=40,
                        help="每张图最多渲染多少个谓词")
    parser.add_argument("--max-width", type=int, default=1100)
    parser.add_argument("--max-height", type=int, default=850)
    parser.add_argument("--sidebar-width", type=int, default=520)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if not args.annotations.is_file():
        print(f"❌ 找不到 annotations：{args.annotations}", file=sys.stderr)
        return 1
    if not args.instances.is_file():
        print(f"❌ 找不到 instances：{args.instances}", file=sys.stderr)
        return 1
    try:
        return run(args)
    except Exception as error:
        print(f"❌ 可视化失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
