"""Visualize hierarchical REC-8K fine-grained subsets.

For every source image and every available hierarchy depth, this module writes one
PNG to ``show`` by default.  The left side contains the source image with colored
subset markers; the right side is a compact legend containing ``attribute`` and
``more_attributes`` for every subset at that depth.

The annotation file is the annotations-format output produced by
``subset_partition.py``.  An entry without ``more_attributes`` is layer 1; an
entry with *n* additional attributes is layer ``n + 1``.

Example::

    python visualize_fine_grained_subsets.py
    python visualize_fine_grained_subsets.py --image-name 0001-fsc147-1306.jpg
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageDraw, ImageFont

DEFAULT_ANNOTATIONS = Path("anno/fine_grained_subsets_20.json")
DEFAULT_IMAGE_DIR = Path("F:/REC-8K/rec-8k")
DEFAULT_OUTPUT_DIR = Path("show_20")

# Fixed, high-contrast review colors.  Unlike a production chart, this review
# view also uses marker shapes and numbered legend rows, so a larger fixed set
# makes dense layers easier to inspect without relying on hue alone.
PALETTE = (
    (0, 95, 115), (202, 103, 2), (155, 34, 38), (10, 147, 150),
    (174, 32, 18), (0, 18, 25), (106, 76, 147), (56, 102, 65),
    (187, 62, 3), (42, 157, 143), (38, 70, 83), (143, 45, 86),
    (127, 85, 57), (58, 90, 64), (95, 15, 64), (29, 53, 87),
    (96, 108, 56), (109, 89, 122), (77, 144, 143), (87, 117, 144),
)

BACKGROUND = (249, 249, 247)
PANEL_BACKGROUND = (252, 252, 251)
PRIMARY_INK = (11, 11, 11)
SECONDARY_INK = (82, 81, 78)
MUTED_INK = (137, 135, 129)
GRIDLINE = (225, 224, 217)
WHITE = (255, 255, 255)

# Shapes are a secondary encoding for layers with many subsets, where a finite
# categorical color palette cannot give every subset a unique hue.
MARKER_SHAPES = ("circle", "square", "diamond", "triangle", "plus")


@dataclass(frozen=True)
class SubsetRecord:
    """A validated annotation entry used by the renderer."""

    key: str
    class_name: str
    attribute: str
    more_attributes: tuple[str, ...]
    points: tuple[tuple[float, float], ...]
    type_name: str | None = None

    @property
    def depth(self) -> int:
        """Return the number of added attributes (zero for the original layer)."""
        return len(self.more_attributes)

    @property
    def layer(self) -> int:
        return self.depth + 1

    @property
    def identity(self) -> str:
        """Stable identity used for color/shape assignment across all layers."""
        payload = "\x1f".join(
            [self.class_name, self.attribute, *self.more_attributes]
        )
        return payload


def _coerce_points(raw_points: Any, *, image_name: str, key: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw_points, (list, tuple)):
        raise ValueError(f"{image_name!r}/{key!r}: points must be a list")

    points: list[tuple[float, float]] = []
    for point_index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            raise ValueError(
                f"{image_name!r}/{key!r}: invalid point at index {point_index}"
            )
        try:
            x, y = float(raw_point[0]), float(raw_point[1])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{image_name!r}/{key!r}: non-numeric point at index {point_index}"
            ) from error
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(
                f"{image_name!r}/{key!r}: non-finite point at index {point_index}"
            )
        points.append((x, y))
    return tuple(points)


def parse_image_annotations(
        image_name: str, image_annotations: Mapping[str, Any]
) -> list[SubsetRecord]:
    """Validate one image's annotations and preserve their JSON order."""
    if not isinstance(image_annotations, Mapping):
        raise ValueError(f"{image_name!r}: image annotations must be an object")

    records: list[SubsetRecord] = []
    for key, raw in image_annotations.items():
        if not isinstance(key, str) or not isinstance(raw, Mapping):
            raise ValueError(f"{image_name!r}: invalid annotation entry {key!r}")

        class_name = str(raw.get("class", ""))
        attribute = str(raw.get("attribute", ""))
        raw_more = raw.get("more_attributes", ())
        if raw_more is None:
            raw_more = ()
        if not isinstance(raw_more, (list, tuple)):
            raise ValueError(f"{image_name!r}/{key!r}: more_attributes must be a list")
        more_attributes = tuple(str(value) for value in raw_more if str(value).strip())

        records.append(
            SubsetRecord(
                key=key,
                class_name=class_name,
                attribute=attribute,
                more_attributes=more_attributes,
                points=_coerce_points(raw.get("points", ()), image_name=image_name, key=key),
                type_name=str(raw["type"]) if raw.get("type") is not None else None,
            )
        )
    return records


def group_records_by_layer(records: Iterable[SubsetRecord]) -> dict[int, list[SubsetRecord]]:
    """Group entries into layer 1, layer 2, ... by added-attribute count."""
    grouped: dict[int, list[SubsetRecord]] = defaultdict(list)
    for record in records:
        grouped[record.layer].append(record)
    return dict(sorted(grouped.items()))


def _style_for_records(records: list[SubsetRecord]) -> dict[str, tuple[tuple[int, int, int], str]]:
    """Assign distinct palette slots in stable identity order for one layer."""
    styles = {}
    ordered = sorted(records, key=lambda record: (record.identity, record.key))
    for index, record in enumerate(ordered):
        styles[record.key] = (PALETTE[index % len(PALETTE)],
                              MARKER_SHAPES[index % len(MARKER_SHAPES)])
    return styles


def _validate_layer_points(image_name: str, layer: int,
                           records: list[SubsetRecord]) -> None:
    """Reject stale/invalid overlapping annotations before drawing."""
    seen: dict[tuple[float, float], str] = {}
    for record in records:
        for point in record.points:
            owner = seen.get(point)
            if owner is not None and owner != record.key:
                raise ValueError(
                    f"{image_name!r} layer {layer} has overlapping subsets at {point}; "
                    "regenerate fine_grained_subsets.json with subset_partition.py"
                )
            seen[point] = record.key


def _font_candidates(bold: bool) -> tuple[Path, ...]:
    if os.name == "nt":
        return (
            Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        )
    return (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )


def load_font(size: int, *, bold: bool = False,
              font_path: Path | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a Unicode-capable font, with a PIL fallback for unusual systems."""
    candidates = (font_path,) if font_path else _font_candidates(bold)
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    try:
        return draw.textlength(text, font=font)
    except AttributeError:  # pragma: no cover - old Pillow compatibility
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]


def wrap_to_width(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
) -> list[str]:
    """Wrap on spaces while also splitting an overlong token by characters."""
    if not text:
        return [""]

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for character in word:
            candidate = current + character
            if current and _text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_marker(
        draw: ImageDraw.ImageDraw,
        center: tuple[float, float],
        radius: int,
        color: tuple[int, int, int],
        shape: str,
) -> None:
    """Draw a high-contrast marker with a white surface ring."""
    x, y = center
    halo = radius + max(2, radius // 3)
    draw.ellipse((x - halo, y - halo, x + halo, y + halo), fill=WHITE)
    if shape == "circle":
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    elif shape == "square":
        draw.rectangle((x - radius, y - radius, x + radius, y + radius), fill=color)
    elif shape == "diamond":
        draw.polygon(((x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)), fill=color)
    elif shape == "triangle":
        draw.polygon(((x, y - radius), (x + radius, y + radius), (x - radius, y + radius)), fill=color)
    else:  # plus
        arm = max(2, radius // 2)
        draw.rectangle((x - arm, y - radius, x + arm, y + radius), fill=color)
        draw.rectangle((x - radius, y - arm, x + radius, y + arm), fill=color)


def _source_path(image_dir: Path, image_name: str) -> Path:
    source = Path(image_name)
    return source if source.is_absolute() else image_dir / source


def _resize_for_display(
        image: Image.Image,
        max_width: int,
        max_height: int,
) -> tuple[Image.Image, float]:
    width, height = image.size
    scale = min(max_width / width, max_height / height)
    # Small REC-8K source images benefit from enlargement in a review figure,
    # while the cap prevents a very large source from dominating the canvas.
    scale = min(max(scale, 1.0), 3.0)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == image.size:
        return image.copy(), 1.0
    return image.resize(size, Image.Resampling.LANCZOS), scale


def _draw_sidebar(
        canvas: Image.Image,
        records: list[SubsetRecord],
        layer: int,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        styles: Mapping[str, tuple[tuple[int, int, int], str]],
        font_path: Path | None = None,
) -> None:
    draw = ImageDraw.Draw(canvas)
    header_font = load_font(22, bold=True, font_path=font_path)
    subheader_font = load_font(14, font_path=font_path)
    row_font_size = 14 if len(records) <= 60 else 12 if len(records) <= 150 else 10
    row_font = load_font(row_font_size, font_path=font_path)
    row_bold_font = load_font(row_font_size, bold=True, font_path=font_path)

    draw.rounded_rectangle((x, y, x + width, y + height), radius=12,
                           fill=PANEL_BACKGROUND, outline=GRIDLINE, width=2)
    padding = 18
    draw.text((x + padding, y + padding), f"Layer {layer} / layer {layer}",
              fill=PRIMARY_INK, font=header_font)
    description = (
        "original attribute only"
        if layer == 1 else f"attribute + {layer - 1} more_attributes"
    )
    draw.text((x + padding, y + padding + 34), description,
              fill=SECONDARY_INK, font=subheader_font)
    draw.text((x + padding, y + padding + 54),
              f"{len(records)} subsets · {sum(len(item.points) for item in records)} points",
              fill=MUTED_INK, font=subheader_font)
    draw.line((x + padding, y + padding + 78, x + width - padding, y + padding + 78),
              fill=GRIDLINE, width=1)

    cursor_y = y + padding + 90
    available_width = width - padding * 2 - 32
    # Use a compact row for high-cardinality layers, but never omit an entry.
    gap = 8 if len(records) <= 60 else 5 if len(records) <= 150 else 3
    for index, record in enumerate(records, start=1):
        color, shape = styles[record.key]
        swatch_center = (x + padding + 10, cursor_y + 11)
        _draw_marker(draw, swatch_center, 7, color, shape)

        label = f"#{index}  attribute: {record.attribute or '—'}"
        more = "; ".join(record.more_attributes) if record.more_attributes else "—"
        details = f"more_attributes: {more}"
        point_text = f"points: {len(record.points)}"
        text_x = x + padding + 25
        label_lines = wrap_to_width(draw, label, row_bold_font, available_width)
        detail_lines = wrap_to_width(draw, details, row_font, available_width)
        # A third line is intentionally reserved for point count so that a
        # human can reconcile the legend with the marker density.
        lines = label_lines + detail_lines + [point_text]
        line_height = row_font_size + 4
        row_height = max(30, len(lines) * line_height + 3)
        if cursor_y + row_height > y + height - padding:
            # This should only happen for an unusually long label.  The canvas
            # is sized from the same row calculation in render_layer, so this
            # branch is a defensive fallback rather than a truncation path.
            break

        line_y = cursor_y
        for line_index, line in enumerate(lines):
            current_font = row_bold_font if line_index < len(label_lines) else row_font
            draw.text((text_x, line_y), line, fill=PRIMARY_INK if line_index < len(label_lines)
            else SECONDARY_INK, font=current_font)
            line_y += line_height
        cursor_y += row_height + gap


def _sidebar_height(
        records: list[SubsetRecord],
        width: int,
        *,
        font_path: Path | None = None,
) -> int:
    """Measure enough vertical space for all legend entries."""
    # Pillow's font metrics are available without a canvas; use a tiny temporary
    # image for text wrapping measurements.
    probe = Image.new("RGB", (width, 10), WHITE)
    draw = ImageDraw.Draw(probe)
    padding = 18
    row_font_size = 14 if len(records) <= 60 else 12 if len(records) <= 150 else 10
    row_font = load_font(row_font_size, font_path=font_path)
    row_bold_font = load_font(row_font_size, bold=True, font_path=font_path)
    available_width = width - padding * 2 - 32
    gap = 8 if len(records) <= 60 else 5 if len(records) <= 150 else 3
    total = padding + 90
    for index, record in enumerate(records, start=1):
        label_lines = wrap_to_width(
            draw, f"#{index}  attribute: {record.attribute or '—'}",
            row_bold_font, available_width
        )
        detail_lines = wrap_to_width(
            draw,
            f"more_attributes: {'; '.join(record.more_attributes) if record.more_attributes else '—'}",
            row_font,
            available_width,
        )
        total += max(30, (len(label_lines) + len(detail_lines) + 1) * (row_font_size + 4) + 3) + gap
    return total + padding


def render_layer(
        image: Image.Image,
        image_name: str,
        records: list[SubsetRecord],
        layer: int,
        *,
        max_image_width: int = 1100,
        max_image_height: int = 900,
        sidebar_width: int = 530,
        font_path: Path | None = None,
) -> Image.Image:
    """Render one layer as an RGB review figure."""
    if not records:
        raise ValueError("cannot render an empty layer")
    _validate_layer_points(image_name, layer, records)
    styles = _style_for_records(records)
    source = image.convert("RGB")
    displayed, scale = _resize_for_display(source, max_image_width, max_image_height)
    image_width, image_height = displayed.size

    title_height = 62
    margin = 24
    gap = 22
    side_height = _sidebar_height(records, sidebar_width, font_path=font_path)
    canvas_width = margin + image_width + gap + sidebar_width + margin
    canvas_height = max(title_height + margin + image_height + margin, side_height + title_height)
    canvas = Image.new("RGB", (canvas_width, canvas_height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    title_font = load_font(24, bold=True, font_path=font_path)
    subtitle_font = load_font(14, font_path=font_path)
    draw.text((margin, 15), f"{Path(image_name).name} - layer {layer}",
              fill=PRIMARY_INK, font=title_font)
    draw.text((margin, 43),
              "Each marker belongs to exactly one subset in this layer.",
              fill=SECONDARY_INK, font=subtitle_font)

    image_x = margin
    image_y = title_height
    draw.rounded_rectangle(
        (image_x - 2, image_y - 2, image_x + image_width + 2, image_y + image_height + 2),
        radius=8, fill=WHITE, outline=GRIDLINE, width=2,
    )
    canvas.paste(displayed, (image_x, image_y))
    marker_draw = ImageDraw.Draw(canvas)

    # Points are validated as disjoint above; the map keeps one marker per point.
    point_memberships: dict[tuple[float, float], list[int]] = defaultdict(list)
    for record_index, record in enumerate(records):
        for point in record.points:
            point_memberships[point].append(record_index)

    marker_radius = max(6, min(16, round(min(image_width, image_height) * 0.018)))
    for point, membership_indices in point_memberships.items():
        x = image_x + point[0] * scale
        y = image_y + point[1] * scale
        if not (-marker_radius <= x <= canvas_width + marker_radius
                and -marker_radius <= y <= canvas_height + marker_radius):
            continue
        record = records[membership_indices[0]]
        color, shape = styles[record.key]
        _draw_marker(marker_draw, (x, y), marker_radius, color, shape)

    sidebar_x = image_x + image_width + gap
    sidebar_y = title_height
    _draw_sidebar(canvas, records, layer, sidebar_x, sidebar_y, sidebar_width,
                  canvas_height - sidebar_y - margin, styles=styles,
                  font_path=font_path)
    return canvas


def output_name(image_name: str, layer: int, extension: str = ".png") -> str:
    """Return ``{original-stem}_layer{n}.png`` without double extensions."""
    return f"{Path(image_name).stem}_layer{layer}{extension}"


def generate_visualizations(
        annotations_path: str | Path = DEFAULT_ANNOTATIONS,
        image_dir: str | Path = DEFAULT_IMAGE_DIR,
        output_dir: str | Path = DEFAULT_OUTPUT_DIR,
        *,
        image_names: Iterable[str] | None = None,
        max_image_width: int = 1100,
        max_image_height: int = 900,
        sidebar_width: int = 530,
        font_path: str | Path | None = None,
        strict: bool = False,
) -> list[Path]:
    """Generate all requested review images and return their paths.

    Missing source images are reported and skipped by default.  ``strict=True``
    turns such a missing file into an exception, which is useful in automation.
    """
    annotations_path = Path(annotations_path)
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)
    if not annotations_path.is_file():
        raise FileNotFoundError(f"annotations file not found: {annotations_path}")
    if max_image_width < 1 or max_image_height < 1 or sidebar_width < 240:
        raise ValueError("image dimensions and sidebar width must be positive")

    import json

    with annotations_path.open("r", encoding="utf-8") as handle:
        annotations = json.load(handle)
    if not isinstance(annotations, Mapping):
        raise ValueError("top-level annotations JSON must be an object")

    selected = set(image_names) if image_names is not None else None
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_font = Path(font_path) if font_path else None
    generated: list[Path] = []

    for image_name, raw_image_annotations in annotations.items():
        if selected is not None and image_name not in selected:
            continue
        source_path = _source_path(image_dir, image_name)
        if not source_path.is_file():
            message = f"skip {image_name}: source image not found at {source_path}"
            if strict:
                raise FileNotFoundError(message)
            print(f"warning: {message}", file=sys.stderr)
            continue

        records = parse_image_annotations(image_name, raw_image_annotations)
        layers = group_records_by_layer(records)
        if not layers:
            print(f"warning: skip {image_name}: no annotation entries", file=sys.stderr)
            continue
        with Image.open(source_path) as source:
            source.load()
            for layer, layer_records in layers.items():
                _validate_layer_points(image_name, layer, layer_records)
                figure = render_layer(
                    source,
                    image_name,
                    layer_records,
                    layer,
                    max_image_width=max_image_width,
                    max_image_height=max_image_height,
                    sidebar_width=sidebar_width,
                    font_path=resolved_font,
                )
                destination = output_dir / output_name(image_name, layer)
                figure.save(destination, format="PNG", optimize=True)
                generated.append(destination)
                print(f"wrote {destination} ({len(layer_records)} subsets)")
    return generated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render REC-8K hierarchical fine-grained subset review figures"
    )
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--image-name", action="append", dest="image_names",
        help="only render this source image; repeat the option for several images",
    )
    parser.add_argument("--max-image-width", type=int, default=1100)
    parser.add_argument("--max-image-height", type=int, default=900)
    parser.add_argument("--sidebar-width", type=int, default=530)
    parser.add_argument("--font-path", type=Path, default=None)
    parser.add_argument(
        "--strict", action="store_true",
        help="fail instead of skipping a missing source image",
    )
    args = parser.parse_args(argv)
    if args.max_image_width < 1 or args.max_image_height < 1 or args.sidebar_width < 240:
        parser.error("--max-image-width、--max-image-height 必须为正数，--sidebar-width 至少为 240")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        generated = generate_visualizations(
            args.annotations,
            args.image_dir,
            args.output_dir,
            image_names=args.image_names,
            max_image_width=args.max_image_width,
            max_image_height=args.max_image_height,
            sidebar_width=args.sidebar_width,
            font_path=args.font_path,
            strict=args.strict,
        )
    except Exception as error:
        print(f"visualization failed: {error}", file=sys.stderr)

        return 1
    print(f"generated {len(generated)} review figures in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
