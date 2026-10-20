"""Shared loading and rendering for step7 visualization and step8 review.

The step6 file contains one caption root with coordinates followed by subset nodes
that refer to stable instance IDs.  This module resolves those IDs once and keeps
the image layout identical in the batch visualizer and the browser reviewer.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from recsub.io import iter_jsonl

POSITIVE_MARKER = (181, 31, 50)
OTHER_MARKER = (25, 158, 112)
# Legacy import name; current subset membership is encoded by a red cross.
YES = OTHER_MARKER
PAGE_BACKGROUND = (249, 249, 247)
SIDEBAR_BACKGROUND = (252, 252, 251)
BORDER = (220, 220, 215)
PRIMARY = (15, 15, 15)
SECONDARY = (75, 75, 75)
MUTED = (120, 120, 120)
TITLE_HEIGHT = 62
MARGIN = 20
PANEL_GAP = 18
PANEL_PADDING = 14
MIN_PANEL_HEIGHT = 420


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
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


def wrap_text(draw: ImageDraw.ImageDraw, text: str, width: int,
              text_font: ImageFont.ImageFont) -> list[str]:
    """Wrap both space-separated English and continuous CJK text."""
    text = str(text or "—")
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=text_font) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or ["—"]


def fit_text(draw: ImageDraw.ImageDraw, text: str, width: int,
             text_font: ImageFont.ImageFont) -> str:
    """Return one-line text that fits a sidebar field."""
    if draw.textlength(text, font=text_font) <= width:
        return text
    while text and draw.textlength(text + "…", font=text_font) > width:
        text = text[:-1]
    return text + "…"


def _key(node: Mapping[str, Any]) -> tuple[str, str]:
    return str(node["image_name"]), str(node["caption"])


def load_subset_nodes(annotations_path: str | Path, *, positive_only: bool = True) -> list[dict]:
    """Load step6 subset nodes and resolve coordinates from caption roots.

    ``positive_only`` excludes complements and negative-polarity nodes.  Roots are
    never returned.  The returned dictionaries are detached copies safe to enrich
    with ``yes_points``/``no_points``/``unknown_points`` by callers.
    """
    roots: dict[tuple[str, str], dict[int, Sequence[float]]] = {}
    subsets: list[dict] = []
    for raw in iter_jsonl(annotations_path):
        kind = raw.get("node_type")
        if kind == "caption_root":
            ids, points = raw.get("instance_ids", ()), raw.get("points", ())
            if len(ids) != len(points):
                raise ValueError(f"根节点 {_key(raw)} 的 instance_ids 与 points 数量不一致")
            roots[_key(raw)] = {int(instance_id): point for instance_id, point in zip(ids, points)}
            continue
        if kind != "predicate_subset":
            continue
        predicate = raw.get("predicate") or {}
        if positive_only and (raw.get("complement_of") or predicate.get("polarity") == "negative"):
            continue
        subsets.append(dict(raw))

    for node in subsets:
        key = _key(node)
        point_by_id = roots.get(key)
        if point_by_id is None:
            raise ValueError(f"子集 {key} 找不到 step6 caption_root 坐标")
        for field, output in (("instance_ids", "yes_points"),
                              ("negative_instance_ids", "no_points"),
                              ("unknown_instance_ids", "unknown_points")):
            ids = [int(value) for value in node.get(field, ())]
            missing = [instance_id for instance_id in ids if instance_id not in point_by_id]
            if missing:
                raise ValueError(f"子集 {key} 的实例 {missing[:5]} 不在 step6 根节点中")
            node[output] = [point_by_id[instance_id] for instance_id in ids]
        yes_ids = {int(value) for value in node.get("instance_ids", ())}
        node["other_instance_ids"] = [i for i in point_by_id if i not in yes_ids]
        node["other_points"] = [point_by_id[i] for i in node["other_instance_ids"]]
    return subsets


def _draw_point(draw: ImageDraw.ImageDraw, point: Sequence[float], scale: float,
                color: tuple[int, int, int], radius: int,
                origin: tuple[int, int]) -> None:
    x = origin[0] + float(point[0]) * scale
    y = origin[1] + float(point[1]) * scale
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill=color, outline="white", width=max(2, radius // 2))


def _draw_cross(draw: ImageDraw.ImageDraw, point: Sequence[float], scale: float,
                origin: tuple[int, int], radius: int = 6) -> None:
    x = origin[0] + float(point[0]) * scale
    y = origin[1] + float(point[1]) * scale
    diagonals = ((x - radius, y - radius, x + radius, y + radius),
                 (x - radius, y + radius, x + radius, y - radius))
    # Paint both halo strokes first so the crossing stays red at its center.
    for color, width in (("white", 8), (POSITIVE_MARKER, 4)):
        for line in diagonals:
            draw.line(line, fill=color, width=width)


def _draw_field(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: Any,
                width: int, *, prominent: bool = False,
                accent: tuple[int, int, int] | None = None) -> int:
    label_size, value_size = (28, 34) if prominent else (24, 26)
    label_font = font(label_size, True)
    value_font = font(value_size, prominent)
    color = accent or SECONDARY
    draw.text((x, y), label, fill=color, font=label_font)
    y += label_size + 7
    value_text = "—" if value is None or value == "" else str(value)
    line_height = value_size + 7
    for line in wrap_text(draw, value_text, width, value_font):
        draw.text((x, y), line, fill=accent or PRIMARY, font=value_font)
        y += line_height
    return y + 14


def render_subset(image: Image.Image, node: Mapping[str, Any], *, subset_index: int = 1,
                  mode: str = "positive", max_width: int = 1100,
                  max_height: int = 850, sidebar_width: int = 520) -> Image.Image:
    """Render exactly one subset with its metadata in a right-hand sidebar.

    Both ``positive`` and ``review`` use red crosses for the current subset and
    green dots for the remaining instances of the same caption root.  These are
    display groups only: unknown labels are not rewritten as no.
    """
    if mode not in {"positive", "review"}:
        raise ValueError("mode 必须是 positive 或 review")
    source = image.convert("RGB")
    scale = min(max_width / source.width, max_height / source.height, 3.0)
    displayed = source.resize((max(1, round(source.width * scale)),
                               max(1, round(source.height * scale))), Image.Resampling.LANCZOS)
    predicate = node.get("predicate") or {}
    other_points = node.get("other_points")
    if other_points is None:
        other_points = [*node.get("no_points", ()), *node.get("unknown_points", ())]
    dimension_value = " / ".join(str(value) for value in (predicate.get("dimension"), predicate.get("value")) if value)
    raw = "、".join(str(value) for value in (predicate.get("raw_phrases") or ()))
    fields = [
        ("原始类别", node.get("category"), True, (25, 78, 150)),
        ("原始 caption", node.get("caption"), True, (135, 48, 92)),
        ("新增划分属性", predicate.get("display") or predicate.get("predicate_id"), True, (0, 112, 84)),
        ("属性维度 / 取值", dimension_value, False, None),
        ("来源 / 原始短语", f"{predicate.get('source') or '—'} / {raw or '—'}", False, None),
        ("子集正样本数（红叉）", len(node.get("instance_ids", node.get("yes_ids", ()))), False, None),
        ("同根其余实例数（绿点）", len(other_points), False, None),
    ]
    if mode == "review":
        review = node.get("review") or {}
        fields.append(("审核状态", review.get("status", "pending"), False, None))
    sidebar_text_width = sidebar_width - 32
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    estimated_text_height = sum(
        ((28 if prominent else 24) + 7)
        + max(1, len(wrap_text(measure, "—" if value is None or value == "" else str(value),
                               sidebar_text_width, font(34 if prominent else 26, prominent))))
          * ((34 if prominent else 26) + 7)
        + 14
        for _, value, prominent, _ in fields
    )
    panel_h = max(displayed.height + PANEL_PADDING * 2,
                  estimated_text_height + PANEL_PADDING * 2 + 14, MIN_PANEL_HEIGHT)
    canvas_h = TITLE_HEIGHT + MARGIN * 2 + panel_h
    canvas = Image.new("RGB", (MARGIN * 2 + displayed.width + PANEL_GAP + sidebar_width, canvas_h),
                       PAGE_BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN, 12), f"REC-8K fine-grained subset #{subset_index}",
              fill=(10, 10, 10), font=font(22, True))
    subtitle = "red crosses = subset positives · green dots = other instances in caption root"
    draw.text((MARGIN, 40), subtitle, fill=(80, 80, 80), font=font(13))

    top = TITLE_HEIGHT + MARGIN
    origin = (MARGIN, top + PANEL_PADDING)
    canvas.paste(displayed, origin)
    draw.rectangle((origin[0] - 1, origin[1] - 1,
                    origin[0] + displayed.width, origin[1] + displayed.height), outline=BORDER)
    # Older review_items.jsonl stored only the complete no/unknown partitions.
    # New items use the exact root-minus-subset coordinates supplied by the loader.
    for point in other_points:
        _draw_point(draw, point, scale, OTHER_MARKER, 5, origin)
    for point in node.get("yes_points", ()):
        _draw_cross(draw, point, scale, origin)

    sidebar_x = MARGIN + displayed.width + PANEL_GAP
    draw.rounded_rectangle((sidebar_x, top, sidebar_x + sidebar_width, top + panel_h),
                           radius=10, fill=SIDEBAR_BACKGROUND, outline=BORDER, width=2)
    text_x = sidebar_x + 16
    cursor = top + 14
    for label, value, prominent, accent in fields:
        cursor = _draw_field(draw, text_x, cursor, label, value, sidebar_text_width,
                             prominent=prominent, accent=accent)
    return canvas


def render_subset_png(image_path: str | Path, node: Mapping[str, Any], *, subset_index: int = 1,
                      mode: str = "positive", max_width: int = 1100,
                      max_height: int = 850, sidebar_width: int = 520) -> bytes:
    """Render one subset to PNG bytes for the review server."""
    with Image.open(image_path) as image:
        rendered = render_subset(image, node, subset_index=subset_index, mode=mode,
                                 max_width=max_width, max_height=max_height,
                                 sidebar_width=sidebar_width)
    output = io.BytesIO()
    rendered.save(output, format="PNG", optimize=True)
    return output.getvalue()
