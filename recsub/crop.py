"""按标注点生成密度自适应的三视图裁剪。

从原 ``crop.py`` 迁入，两处实质改动：

1. **标记样式**。原实现是「轮廓环 + 环外四根引线」，环本身不填充，但环上
   叠了一圈最宽 7px 的白色高光；对密集场景里边界只有十几像素的小目标，
   这圈高光正好压在目标的轮廓上。新增 ``marker_style="outline"``：环半径更
   小、描边更细、高光收窄到与描边同宽、引线更短，目标是尽量少覆盖判定所
   要看的像素。需要说明的是——标注只给了一个点，**无法求出目标的真实轮廓**，
   所以这里能做的只是「少遮挡」，不是「沿物体描边」。
   ``marker_style="ring"`` 保留原行为，便于与旧结果对比。

2. **局部视图分辨率**。fine / context 从 512 提到 768，小目标的部件需要更多
   像素才能判读。
"""

from __future__ import annotations

import heapq
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from PIL import Image, ImageDraw

# ============================================================
# 一、基础类型：统一点坐标、分组键和尺度来源的表示
# ============================================================
# 标注点使用原图像素坐标，顺序固定为 (x, y)。
Point = tuple[float, float]
# 密度只在「同一张图、同一个类别」内估计，避免无关类别干扰目标尺度。
GroupKey = tuple[str, str]
ScaleSource = Literal[
    "knn",
    "sparse_fallback",
    "missing_group_fallback",
    "missing_target_fallback",
]
MarkerStyle = Literal["ring", "outline"]
MARKER_STYLES: tuple[str, ...] = ("ring", "outline")


# ============================================================
# 二、裁剪参数与返回数据结构
# ============================================================
@dataclass(frozen=True, slots=True)
class CropConfig:
    """Configuration for point-guided adaptive crops."""

    # 近邻数量和最少有效邻点共同决定是否信任局部密度。
    neighbor_k: int = 5
    min_neighbors: int = 3
    scale_factor: float = 1.5
    fine_min_ratio: float = 0.08
    fine_max_ratio: float = 0.30
    fine_min_pixels: int = 32
    sparse_fallback_ratio: float = 0.25
    context_multiplier: float = 2.0

    # 两级局部视图的输出边长；从 512 提到 768 以保留小目标的部件细节。
    fine_output_size: int | None = 768
    context_output_size: int | None = 768

    # ---- 标记 ----
    marker_style: MarkerStyle = "outline"
    marker_color: tuple[int, int, int, int] = (255, 32, 32, 235)
    marker_halo_color: tuple[int, int, int, int] = (255, 255, 255, 245)

    # ring 样式（原实现）的参数。
    marker_radius_ratio: float = 0.025
    marker_radius_min: int = 6
    marker_radius_max: int = 18
    marker_stroke_ratio: float = 0.24
    marker_stroke_min: int = 2
    marker_stroke_max: int = 5

    # outline 样式：更紧的环、更细的描边、更窄的高光、更短的引线。
    outline_radius_ratio: float = 0.014
    outline_radius_min: int = 5
    outline_radius_max: int = 14
    outline_stroke_ratio: float = 0.18
    outline_stroke_min: int = 2
    outline_stroke_max: int = 4
    outline_leader_gap: int = 3

    # 全图视图共用一套较大的半径，保证在整图尺度上也能一眼找到目标。
    full_marker_radius_ratio: float = 0.02
    full_marker_radius_min: int = 18
    full_marker_radius_max: int = 72

    def __post_init__(self) -> None:
        # 在构造配置时尽早发现参数错误，避免批量运行到中途才失败。
        if self.neighbor_k < 1:
            raise ValueError("neighbor_k must be at least 1")
        if not 1 <= self.min_neighbors <= self.neighbor_k:
            raise ValueError("min_neighbors must be between 1 and neighbor_k")
        if self.scale_factor <= 0:
            raise ValueError("scale_factor must be positive")
        if not 0 < self.fine_min_ratio <= self.fine_max_ratio <= 1:
            raise ValueError("fine crop ratios must satisfy 0 < min <= max <= 1")
        if self.fine_min_pixels < 1:
            raise ValueError("fine_min_pixels must be at least 1")
        if not 0 < self.sparse_fallback_ratio <= 1:
            raise ValueError("sparse_fallback_ratio must be in (0, 1]")
        if self.context_multiplier < 1:
            raise ValueError("context_multiplier must be at least 1")
        for name in ("fine_output_size", "context_output_size"):
            size = getattr(self, name)
            if size is not None and size < 1:
                raise ValueError(f"{name} must be None or a positive integer")
        if self.marker_style not in MARKER_STYLES:
            raise ValueError(f"marker_style must be one of {MARKER_STYLES}")
        for prefix in ("marker", "outline"):
            if not 0 < getattr(self, f"{prefix}_radius_ratio") <= 0.5:
                raise ValueError(f"{prefix}_radius_ratio must be in (0, 0.5]")
        if not 1 <= self.marker_radius_min <= self.marker_radius_max:
            raise ValueError("invalid marker radius bounds")
        if not 1 <= self.outline_radius_min <= self.outline_radius_max:
            raise ValueError("invalid outline radius bounds")
        if not 0 < self.marker_stroke_ratio <= 1 or not 0 < self.outline_stroke_ratio <= 1:
            raise ValueError("marker_stroke_ratio must be in (0, 1]")
        if not 1 <= self.marker_stroke_min <= self.marker_stroke_max:
            raise ValueError("invalid marker stroke bounds")
        if not 1 <= self.outline_stroke_min <= self.outline_stroke_max:
            raise ValueError("invalid outline stroke bounds")
        if self.outline_leader_gap < 0:
            raise ValueError("outline_leader_gap must not be negative")
        if not 0 < self.full_marker_radius_ratio <= 0.5:
            raise ValueError("full_marker_radius_ratio must be in (0, 0.5]")
        if not 1 <= self.full_marker_radius_min <= self.full_marker_radius_max:
            raise ValueError("invalid full marker radius bounds")
        _validate_rgba(self.marker_color, "marker_color")
        _validate_rgba(self.marker_halo_color, "marker_halo_color")


@dataclass(frozen=True, slots=True)
class CropBox:
    """A PIL-compatible half-open crop box."""

    # PIL 的 right/bottom 是开区间，因此 width=right-left。
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def side(self) -> int:
        if self.width != self.height:
            raise ValueError("crop box is not square")
        return self.width

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True, slots=True)
class CropMetadata:
    """记录尺度选择和坐标修正，便于检查异常标注与批量结果。"""

    image_name: str
    category: str
    original_point: Point
    clamped_point: Point
    point_was_clamped: bool
    scale_source: ScaleSource
    unique_group_points: int
    available_neighbors: int
    neighbors_used: int
    median_neighbor_distance: float | None
    fine_side: int
    context_side: int
    fine_box: CropBox
    context_box: CropBox
    context_marker_point: tuple[int, int]
    fine_output_size: tuple[int, int]
    context_output_size: tuple[int, int]
    marker_style: str


@dataclass(frozen=True, slots=True)
class AdaptiveCrops:
    """返回 Full、Context（带标记）和 Fine（不带标记）三张图。"""

    full_image: Image.Image
    context_image: Image.Image
    fine_image: Image.Image
    metadata: CropMetadata


@dataclass(frozen=True, slots=True)
class _DensityEstimate:
    """单个点的预计算近邻统计；None 表示使用稀疏回退尺度。"""

    unique_group_points: int
    available_neighbors: int
    neighbors_used: int
    median_neighbor_distance: float | None


class AdaptiveCropper:
    """Build density-aware Fine and Context crops around annotated points."""

    # 该对象建立后只读，可安全地在多个异步请求之间共享。

    def __init__(
            self,
            density_index: Mapping[GroupKey, Mapping[Point, _DensityEstimate]],
            config: CropConfig | None = None,
            *,
            instance_point_count: int = 0,
            duplicate_point_count: int = 0,
    ) -> None:
        self.config = config or CropConfig()
        # 用 MappingProxyType 包装索引，防止并发任务意外修改密度数据。
        self._density_index = MappingProxyType(
            {
                key: MappingProxyType(dict(group_estimates))
                for key, group_estimates in density_index.items()
            }
        )
        self.instance_point_count = instance_point_count
        self.unique_point_count = sum(len(group) for group in self._density_index.values())
        self.duplicate_point_count = duplicate_point_count

    @classmethod
    def from_annotations(
            cls,
            annotations: Mapping[str, Mapping[str, Mapping[str, Any]]],
            config: CropConfig | None = None,
    ) -> AdaptiveCropper:
        """Build an index directly from the nested annotations.json schema."""
        instances = (
            {
                "image_name": image_name,
                "category": record.get("class"),
                "point": point,
            }
            for image_name, image_annotations in annotations.items()
            for record in image_annotations.values()
            for point in record.get("points", ())
        )
        return cls.from_instances(instances, config)

    @classmethod
    def from_instances(
            cls,
            instances: Iterable[Mapping[str, Any]],
            config: CropConfig | None = None,
    ) -> AdaptiveCropper:
        """Build a read-only same-image, same-category density index once."""
        resolved_config = config or CropConfig()
        point_groups: dict[GroupKey, set[Point]] = defaultdict(set)
        instance_point_count = 0
        duplicate_point_count = 0

        for index, instance in enumerate(instances):
            try:
                image_name = instance["image_name"]
                category = instance["category"]
                raw_point = instance["point"]
            except (KeyError, TypeError) as exc:
                raise ValueError(f"invalid instance at index {index}: missing crop fields") from exc

            if not isinstance(image_name, str) or not image_name:
                raise ValueError(f"invalid image_name at instance index {index}")
            if not isinstance(category, str) or not category:
                raise ValueError(f"invalid category at instance index {index}")

            point = _coerce_point(raw_point, f"instance point at index {index}")
            group = point_groups[(image_name, category)]
            instance_point_count += 1
            # 重复点仍计入原始实例统计，但从唯一点集合排除，避免零距离污染 kNN。
            if point in group:
                duplicate_point_count += 1
            else:
                group.add(point)

        # 预计算所有唯一点的近邻统计，将昂贵计算从每次请求移到初始化阶段。
        density_index: dict[GroupKey, dict[Point, _DensityEstimate]] = {}
        for key, point_set in point_groups.items():
            points = tuple(sorted(point_set))
            density_index[key] = _estimate_group_density(points, resolved_config)

        return cls(
            density_index,
            resolved_config,
            instance_point_count=instance_point_count,
            duplicate_point_count=duplicate_point_count,
        )

    @property
    def group_count(self) -> int:
        return len(self._density_index)

    def create_crops(
            self,
            image: Image.Image,
            image_name: str,
            category: str,
            point: tuple[float, float] | list[float],
    ) -> AdaptiveCrops:
        """Create a marked Full image, marked Context crop, and unmarked Fine crop."""
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError("image must have positive dimensions")
        if not isinstance(image_name, str) or not image_name:
            raise ValueError("image_name must be a non-empty string")
        if not isinstance(category, str) or not category:
            raise ValueError("category must be a non-empty string")

        original_point = _coerce_point(point, "target point")
        # 越界点保留在 metadata 中，同时把实际裁剪中心限制到合法像素范围。
        clamped_point = (
            min(max(original_point[0], 0.0), float(width - 1)),
            min(max(original_point[1], 0.0), float(height - 1)),
        )
        point_was_clamped = clamped_point != original_point

        key = (image_name, category)
        group = self._density_index.get(key)
        if group is None:
            # 标注索引缺失时不能中断整批任务，使用明确的保守回退尺度。
            estimate = _DensityEstimate(0, 0, 0, None)
            scale_source: ScaleSource = "missing_group_fallback"
        else:
            estimate = group.get(original_point)
            if estimate is None:
                estimate = _DensityEstimate(len(group), max(0, len(group) - 1), 0, None)
                scale_source = "missing_target_fallback"
            elif estimate.median_neighbor_distance is None:
                scale_source = "sparse_fallback"
            else:
                scale_source = "knn"

        shorter_side = min(width, height)
        # Fine 尺度按图像短边归一化，并设置上下限，防止密集/稀疏场景过度缩放。
        fine_min = min(
            shorter_side,
            max(
                self.config.fine_min_pixels,
                math.ceil(shorter_side * self.config.fine_min_ratio),
            ),
        )
        fine_max = min(
            shorter_side,
            max(fine_min, math.floor(shorter_side * self.config.fine_max_ratio)),
        )

        if estimate.median_neighbor_distance is None:
            # 近邻不足时，单个距离不稳定，因此回退到固定比例。
            raw_fine_side = shorter_side * self.config.sparse_fallback_ratio
        else:
            raw_fine_side = estimate.median_neighbor_distance * self.config.scale_factor

        fine_side = _clamp_int(_round_half_up(raw_fine_side), fine_min, fine_max)
        context_side = min(
            shorter_side,
            max(fine_side, _round_half_up(fine_side * self.config.context_multiplier)),
        )

        fine_box = _square_box(clamped_point, fine_side, width, height)
        context_box = _square_box(clamped_point, context_side, width, height)

        # 先裁剪，再统一到 MLLM 输入尺寸；resize 后才计算 marker 的新坐标。
        fine_image = image.crop(fine_box.as_tuple()).convert("RGB")
        context_image = image.crop(context_box.as_tuple()).convert("RGB")

        # 两级局部视图各自统一尺寸，降低不同源图分辨率对 MLLM 的影响。
        fine_image = _resize_output(fine_image, self.config.fine_output_size)
        context_image = _resize_output(context_image, self.config.context_output_size)

        marker_point = _point_in_output(clamped_point, context_box, context_image.size)
        # marker 只画在 Full 和 Context 上；Fine 保持干净，供模型观察细节。
        context_image = _draw_target_marker(context_image, marker_point, self.config)
        # Full 使用原图坐标直接标记；_draw_target_marker 通过 overlay 不会修改输入 image。
        full_point = (
            _clamp_int(_round_half_up(clamped_point[0]), 0, width - 1),
            _clamp_int(_round_half_up(clamped_point[1]), 0, height - 1),
        )
        # Full 与 Context 使用同一套样式，仅按各自图像尺寸选择 marker 大小。
        full_image = _draw_target_marker(
            image,
            full_point,
            self.config,
            radius_ratio=self.config.full_marker_radius_ratio,
            radius_min=self.config.full_marker_radius_min,
            radius_max=self.config.full_marker_radius_max,
        )

        metadata = CropMetadata(
            image_name=image_name,
            category=category,
            original_point=original_point,
            clamped_point=clamped_point,
            point_was_clamped=point_was_clamped,
            scale_source=scale_source,
            unique_group_points=estimate.unique_group_points,
            available_neighbors=estimate.available_neighbors,
            neighbors_used=estimate.neighbors_used,
            median_neighbor_distance=estimate.median_neighbor_distance,
            fine_side=fine_side,
            context_side=context_side,
            fine_box=fine_box,
            context_box=context_box,
            context_marker_point=marker_point,
            fine_output_size=fine_image.size,
            context_output_size=context_image.size,
            marker_style=self.config.marker_style,
        )
        return AdaptiveCrops(full_image, context_image, fine_image, metadata)


# ============================================================
# 三、密度估计与几何
# ============================================================
def _estimate_group_density(
        points: tuple[Point, ...],
        config: CropConfig,
) -> dict[Point, _DensityEstimate]:
    # 这里使用平方距离找近邻，只有最终选中的距离才开方，减少计算量。
    unique_count = len(points)
    available_neighbors = max(0, unique_count - 1)
    estimates: dict[Point, _DensityEstimate] = {}

    for point_index, point in enumerate(points):
        if available_neighbors < config.min_neighbors:
            # 稀疏组缺乏稳定的局部密度信号，交给调用方使用 fallback。
            estimates[point] = _DensityEstimate(
                unique_group_points=unique_count,
                available_neighbors=available_neighbors,
                neighbors_used=0,
                median_neighbor_distance=None,
            )
            continue

        nearest_count = min(config.neighbor_k, available_neighbors)
        squared_distances = (
            (point[0] - other[0]) ** 2 + (point[1] - other[1]) ** 2
            for other_index, other in enumerate(points)
            if other_index != point_index
        )
        nearest_squared = heapq.nsmallest(nearest_count, squared_distances)
        nonzero_distances = [math.sqrt(distance) for distance in nearest_squared if distance > 0]

        if len(nonzero_distances) < config.min_neighbors:
            estimates[point] = _DensityEstimate(
                unique_group_points=unique_count,
                available_neighbors=available_neighbors,
                neighbors_used=0,
                median_neighbor_distance=None,
            )
            continue

        estimates[point] = _DensityEstimate(
            unique_group_points=unique_count,
            available_neighbors=available_neighbors,
            neighbors_used=len(nonzero_distances),
            median_neighbor_distance=statistics.median(nonzero_distances),
        )

    return estimates


def _square_box(point: Point, side: int, width: int, height: int) -> CropBox:
    # 通过平移而不是截短来处理边界，使 Fine/Context 始终保持正方形。
    side = _clamp_int(side, 1, min(width, height))
    left = _round_half_up(point[0] - side / 2)
    top = _round_half_up(point[1] - side / 2)
    left = _clamp_int(left, 0, width - side)
    top = _clamp_int(top, 0, height - side)
    return CropBox(left, top, left + side, top + side)


def _resize_output(image: Image.Image, output_size: int | None) -> Image.Image:
    if output_size is None:
        return image
    return image.resize((output_size, output_size), Image.Resampling.LANCZOS)


def _point_in_output(
        point: Point,
        crop_box: CropBox,
        output_size: tuple[int, int],
) -> tuple[int, int]:
    # source crop 使用原图坐标，marker 需要按实际输出尺寸重新映射。
    output_width, output_height = output_size
    output_x = ((point[0] - crop_box.left + 0.5) * output_width / crop_box.width) - 0.5
    output_y = ((point[1] - crop_box.top + 0.5) * output_height / crop_box.height) - 0.5
    return (
        _clamp_int(_round_half_up(output_x), 0, output_width - 1),
        _clamp_int(_round_half_up(output_y), 0, output_height - 1),
    )


# ============================================================
# 四、目标标记
# ============================================================
def _draw_target_marker(
        image: Image.Image,
        point: tuple[int, int],
        config: CropConfig,
        radius_ratio: float | None = None,
        radius_min: int | None = None,
        radius_max: int | None = None,
) -> Image.Image:
    # 使用独立 RGBA overlay，alpha 合成后返回 RGB，兼容后续 JPEG 编码。
    if config.marker_style == "outline":
        return _draw_outline_marker(image, point, config, radius_ratio, radius_min, radius_max)
    return _draw_ring_marker(image, point, config, radius_ratio, radius_min, radius_max)


def _draw_ring_marker(
        image: Image.Image,
        point: tuple[int, int],
        config: CropConfig,
        radius_ratio: float | None = None,
        radius_min: int | None = None,
        radius_max: int | None = None,
) -> Image.Image:
    """原实现：轮廓环 + 较宽白色高光 + 环外四根引线。"""
    marked_image = image.convert("RGBA")
    overlay = Image.new("RGBA", marked_image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    shorter_side = min(marked_image.size)
    radius = _clamp_int(
        _round_half_up(shorter_side * (radius_ratio or config.marker_radius_ratio)),
        radius_min or config.marker_radius_min,
        radius_max or config.marker_radius_max,
    )
    stroke = _clamp_int(
        _round_half_up(radius * config.marker_stroke_ratio),
        config.marker_stroke_min,
        config.marker_stroke_max,
    )
    center_x, center_y = point
    ring_box = (
        center_x - radius,
        center_y - radius,
        center_x + radius,
        center_y + radius,
    )

    halo_width = max(3, min(config.marker_stroke_max + 2, radius // 2))
    draw.ellipse(ring_box, outline=config.marker_halo_color, width=halo_width)
    draw.ellipse(ring_box, outline=config.marker_color, width=stroke)

    arm_gap = radius + max(2, stroke)
    arm_end = arm_gap + max(4, radius // 2)
    halo_lines = [
        (center_x - arm_end, center_y, center_x - arm_gap, center_y),
        (center_x + arm_gap, center_y, center_x + arm_end, center_y),
        (center_x, center_y - arm_end, center_x, center_y - arm_gap),
        (center_x, center_y + arm_gap, center_x, center_y + arm_end),
    ]
    for line in halo_lines:
        draw.line(line, fill=config.marker_halo_color, width=halo_width)
    for line in halo_lines:
        draw.line(line, fill=config.marker_color, width=stroke)

    return Image.alpha_composite(marked_image, overlay).convert("RGB")


def _draw_outline_marker(
        image: Image.Image,
        point: tuple[int, int],
        config: CropConfig,
        radius_ratio: float | None = None,
        radius_min: int | None = None,
        radius_max: int | None = None,
) -> Image.Image:
    """收紧版标记：更小的环、更细的描边、与描边同宽的窄高光、更短的引线。

    标注只有一个点，无法得到目标真实轮廓；这里的目标是**减少覆盖**——
    原实现那圈最宽 7px 的白色高光会正好压在密集场景里小目标的边界上。
    """
    marked_image = image.convert("RGBA")
    overlay = Image.new("RGBA", marked_image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    shorter_side = min(marked_image.size)
    # 全图视图沿用较大的半径，局部视图用收得更紧的 outline 半径。
    if radius_ratio is None:
        radius_ratio = config.outline_radius_ratio
    if radius_min is None:
        radius_min = config.outline_radius_min
    if radius_max is None:
        radius_max = config.outline_radius_max
    radius = _clamp_int(
        _round_half_up(shorter_side * radius_ratio), radius_min, radius_max
    )
    stroke = _clamp_int(
        _round_half_up(radius * config.outline_stroke_ratio),
        config.outline_stroke_min,
        config.outline_stroke_max,
    )
    # 高光只比描边宽一点：既保住低对比度背景上的可见性，又不遮住目标边界。
    halo_width = stroke + 1

    center_x, center_y = point
    ring_box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
    draw.ellipse(ring_box, outline=config.marker_halo_color, width=halo_width)
    draw.ellipse(ring_box, outline=config.marker_color, width=stroke)

    # 引线从环外一小段距离开始，长度约等于环半径，不跨越目标本体。
    arm_gap = radius + stroke + config.outline_leader_gap
    arm_end = arm_gap + max(3, radius)
    halo_lines = [
        (center_x - arm_end, center_y, center_x - arm_gap, center_y),
        (center_x + arm_gap, center_y, center_x + arm_end, center_y),
        (center_x, center_y - arm_end, center_x, center_y - arm_gap),
        (center_x, center_y + arm_gap, center_x, center_y + arm_end),
    ]
    for line in halo_lines:
        draw.line(line, fill=config.marker_halo_color, width=halo_width + 1)
    for line in halo_lines:
        draw.line(line, fill=config.marker_color, width=stroke)

    return Image.alpha_composite(marked_image, overlay).convert("RGB")


# ============================================================
# 五、工具函数
# ============================================================
def _coerce_point(value: Any, label: str) -> Point:
    if isinstance(value, (str, bytes)):
        # 字符串虽然可索引，但不是合法的二维坐标序列。
        raise ValueError(f"{label} must be a two-number sequence")
    try:
        if len(value) != 2:
            raise ValueError
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"{label} must be a two-number sequence") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError(f"{label} coordinates must be finite")
    return x, y


def _validate_rgba(value: tuple[int, int, int, int], label: str) -> None:
    if len(value) != 4 or any(
            not isinstance(channel, int) or not 0 <= channel <= 255
            for channel in value
    ):
        raise ValueError(f"{label} must contain four integer channels in [0, 255]")


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return min(max(value, lower), upper)


# ============================================================
# 六、直接运行脚本时，随机生成 10 组裁剪可视化结果（含两种标记样式对照）
# ============================================================
def _fit_to_visualization_panel(image: Image.Image, panel_size: int) -> Image.Image:
    """等比例缩放图片并居中放入正方形画布，避免拉伸原图。"""
    image = image.convert("RGB")
    fitted = image.copy()
    fitted.thumbnail((panel_size, panel_size), Image.Resampling.LANCZOS)

    panel = Image.new("RGB", (panel_size, panel_size), (245, 245, 245))
    left = (panel_size - fitted.width) // 2
    top = (panel_size - fitted.height) // 2
    panel.paste(fitted, (left, top))
    return panel


def _compose_crop_visualization(
        full_image: Image.Image,
        context_image: Image.Image,
        fine_image: Image.Image,
        panel_size: int = 512,
) -> Image.Image:
    """从左到右拼接 Full、Context、Fine，并在顶部标出视图名称。"""
    labels = ("Full", "Context", "Fine")
    images = (full_image, context_image, fine_image)
    label_height = 44
    gap = 8
    canvas_width = panel_size * len(images) + gap * (len(images) - 1)
    canvas_height = label_height + panel_size
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(canvas)

    for index, (label, image) in enumerate(zip(labels, images)):
        left = index * (panel_size + gap)
        panel = _fit_to_visualization_panel(image, panel_size)
        canvas.paste(panel, (left, label_height))

        text_box = draw.textbbox((0, 0), label)
        text_width = text_box[2] - text_box[0]
        text_left = left + (panel_size - text_width) // 2
        draw.text((text_left, 13), label, fill="black")

    return canvas


def _visualize_random_samples(sample_count: int = 10) -> None:
    """从 instance_data.jsonl 随机抽样，把三视图拼图存到 ./tmp 供人工查看。"""
    import json
    import random
    from pathlib import Path

    from recsub.config import CONFIG, crop_config, path

    instance_path = path("instance_data")
    image_dir = Path(CONFIG["image_dir"])
    output_dir = Path("./tmp")
    output_dir.mkdir(parents=True, exist_ok=True)

    with instance_path.open("r", encoding="utf-8") as file:
        instance_data = [json.loads(line) for line in file if line.strip()]
    if not instance_data:
        raise ValueError(f"实例标注为空: {instance_path}")

    # 密度必须由全量点构建；随机抽样只决定可视化哪些实例。
    cropper = AdaptiveCropper.from_instances(instance_data, crop_config())
    samples = random.sample(instance_data, k=min(sample_count, len(instance_data)))

    for sample_index, instance in enumerate(samples, start=1):
        image_path = image_dir / instance["image_name"]
        with Image.open(image_path) as source_image:
            full_image = source_image.convert("RGB")
            crops = cropper.create_crops(
                full_image,
                image_name=instance["image_name"],
                category=instance["category"],
                point=instance["point"],
            )
            visualization = _compose_crop_visualization(
                crops.full_image,
                crops.context_image,
                crops.fine_image,
            )

        instance_id = instance.get("instance_id", sample_index)
        output_path = output_dir / f"sample_{sample_index:02d}_instance_{instance_id}.jpg"
        visualization.save(output_path, format="JPEG", quality=95)
        print(f"Saved: {output_path}")


if __name__ == "__main__":
    _visualize_random_samples(sample_count=10)
