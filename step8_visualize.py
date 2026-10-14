"""step8：可视化 step6 选出的细粒度子集。

每张图片只展示一个 step6 正向子集，用点标记其 instance_ids（yes 支持集）。
互补子集不生成图片。同一实例出现在多个正向子集时，各自生成独立图片，
标记不会相互遮盖。这仍是教师伪标签，并非人工金标准。
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

YES = (15, 150, 90)
TITLE_HEIGHT = 62
MARGIN = 20
PANEL_GAP = 18
PANEL_PADDING = 14
MIN_PANEL_HEIGHT = 168


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


def draw_point(draw: ImageDraw.ImageDraw, point: Sequence[float], scale: float,
               color: tuple[int, int, int], radius: int,
               origin: tuple[int, int]) -> None:
    x = origin[0] + float(point[0]) * scale
    y = origin[1] + float(point[1]) * scale
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill=color, outline="white", width=max(1, radius // 3))


def fit_text(draw: ImageDraw.ImageDraw, text: str, width: int, text_font: ImageFont.ImageFont) -> str:
    """Keep sidebar text inside its own predicate panel."""
    if draw.textlength(text, font=text_font) <= width:
        return text
    while text and draw.textlength(text + "…", font=text_font) > width:
        text = text[:-1]
    return text + "…"


def render_page(image: Image.Image, image_name: str, caption: str,
                node: Mapping[str, Any], *, subset_index: int,
                max_width: int = 1100, max_height: int = 850,
                sidebar_width: int = 520) -> Image.Image:
    source = image.convert("RGB")
    scale = min(max_width / source.width, max_height / source.height, 3.0)
    displayed = source.resize((max(1, round(source.width * scale)),
                               max(1, round(source.height * scale))), Image.Resampling.LANCZOS)
    panel_h = max(displayed.height + PANEL_PADDING * 2, MIN_PANEL_HEIGHT)
    canvas_h = TITLE_HEIGHT + MARGIN * 2 + panel_h
    canvas = Image.new("RGB", (MARGIN * 2 + displayed.width + PANEL_GAP + sidebar_width, canvas_h),
                       (249, 249, 247))
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN, 12), f"{Path(image_name).name} · {caption} · subset {subset_index}",
              fill=(10, 10, 10), font=font(22, True))
    draw.text((MARGIN, 40), "step6 subset · green dots = included instances · pseudo-labels",
              fill=(80, 80, 80), font=font(13))
    top = TITLE_HEIGHT + MARGIN
    origin = (MARGIN, top + PANEL_PADDING)
    canvas.paste(displayed, origin)
    draw.rectangle((origin[0] - 1, origin[1] - 1,
                    origin[0] + displayed.width, origin[1] + displayed.height),
                   outline=(220, 220, 215))
    # 只画当前 step6 子集的 instance_ids；其他子集不参与这张图。
    for point in node.get("yes_points", ()):
        draw_point(draw, point, scale, YES, 7, origin)

    sidebar_x = MARGIN + displayed.width + PANEL_GAP
    draw.rounded_rectangle((sidebar_x, top, sidebar_x + sidebar_width, top + panel_h),
                           radius=10, fill=(252, 252, 251), outline=(220, 220, 215), width=2)
    predicate = node.get("predicate") or {}
    text_x = sidebar_x + 16
    text_width = sidebar_width - 32
    label = f"#{subset_index} {predicate.get('display') or predicate.get('predicate_id', '—')}"
    draw.text((text_x, top + 14), fit_text(draw, label, text_width, font(16, True)),
              fill=(15, 15, 15), font=font(16, True))
    draw.ellipse((text_x, top + 51, text_x + 11, top + 62), fill=YES)
    draw.text((text_x + 16, top + 47), f"subset instances: {len(node.get('instance_ids', ()))}",
              fill=(75, 75, 75), font=font(12))
    source_label = "intersection subset" if predicate.get("source") == "intersection" else "selected subset"
    draw.text((text_x, top + 80), source_label, fill=(75, 75, 75), font=font(12))
    raw = ", ".join(predicate.get("raw_phrases") or ()) or "—"
    draw.text((text_x, top + 107), fit_text(draw, "raw: " + raw, text_width, font(11)),
              fill=(120, 120, 120), font=font(11))
    return canvas


def run(args: argparse.Namespace) -> int:
    roots: dict[tuple[str, str], dict[int, Sequence[float]]] = {}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for node in iter_jsonl(args.annotations):
        key = (str(node["image_name"]), str(node["caption"]))
        if node.get("node_type") == "caption_root":
            ids, points = node["instance_ids"], node["points"]
            if len(ids) != len(points):
                raise ValueError(f"根节点 {key} 的 instance_ids 与 points 数量不一致")
            roots[key] = {int(i): point for i, point in zip(ids, points)}
        elif (node.get("node_type") == "predicate_subset"
              and not node.get("complement_of")
              and (node.get("predicate") or {}).get("polarity") != "negative"):
            groups[key].append(dict(node))
    for key, nodes in groups.items():
        if key not in roots:
            raise ValueError(f"子集 {key} 找不到 step6 caption_root 坐标")
        for node in nodes:
            ids = [int(i) for i in node.get("instance_ids", ())]
            missing = [i for i in ids if i not in roots[key]]
            if missing:
                raise ValueError(f"子集 {key} 的实例 {missing[:5]} 不在 step6 根节点中")
            node["yes_points"] = [roots[key][i] for i in ids]
    if args.image_names:
        allowed = {name.strip() for name in args.image_names.split(",") if name.strip()}
        groups = {key: value for key, value in groups.items() if key[0] in allowed}
    if not groups:
        print("没有可视化的 step6 子集节点")
        return 0

    if min(args.max_width, args.max_height, args.sidebar_width) <= 0:
        raise ValueError("max-width、max-height 和 sidebar-width 必须大于 0")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for (image_name, caption), nodes in sorted(groups.items()):
        image_path = args.image_dir / image_name
        if not image_path.is_file():
            print(f"⚠️ 找不到图片，跳过：{image_path}", file=sys.stderr)
            continue
        with Image.open(image_path) as source:
            for subset_index, node in enumerate(nodes, start=1):
                figure = render_page(source, image_name, caption, node, subset_index=subset_index,
                                     max_width=args.max_width, max_height=args.max_height,
                                     sidebar_width=args.sidebar_width)
                # step6 的 node_id 是根与谓词的稳定哈希，同名图片/截断 caption 也不会覆盖。
                destination = args.output_dir / f"{Path(image_name).stem}__{node['node_id']}.png"
                figure.save(destination, format="PNG", optimize=True)
                written += 1
    print(f"✅ 生成 {written} 张 step6 子集图 → {args.output_dir}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染 step6 细粒度子集（仅 instance_ids）")
    parser.add_argument("--annotations", type=Path, default=path("subsets"),
                        help="step6 子集节点 JSONL")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--output-dir", type=Path, default=path("output_dir") / "positive")
    parser.add_argument("--image-names", default=None, help="只渲染逗号分隔图片")
    parser.add_argument("--max-width", type=int, default=CONFIG["visualize"]["max_width"])
    parser.add_argument("--max-height", type=int, default=CONFIG["visualize"]["max_height"])
    parser.add_argument("--sidebar-width", type=int, default=CONFIG["visualize"]["sidebar_width"])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
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
