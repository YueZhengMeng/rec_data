"""检查子集可视化对重叠子集、分层和缺图情况的行为。"""

import json
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from visualize_fine_grained_subsets import (
    GROUP_RING,
    PALETTE,
    generate_visualizations,
    group_records_by_layer,
    parse_image_annotations,
    point_memberships,
    render_layer,
    subset_summaries,
)


def annotations_with_overlap():
    """一个父 caption 加上两个重叠子集：点 [10, 10] 同时属于两者。"""
    return {
        "sample.jpg": {
            "red pen": {"class": "pen", "attribute": "red", "type": "color",
                        "points": [[10, 10], [40, 10], [70, 10]]},
            "pen red white cap": {"class": "pen", "attribute": "red", "type": "color",
                                  "more_attributes": ["white cap"],
                                  "points": [[10, 10], [40, 10]], "complete": True},
            "pen red black band": {"class": "pen", "attribute": "red", "type": "color",
                                   "more_attributes": ["black band"],
                                   "points": [[10, 10], [70, 10]], "complete": False},
        }
    }


class ParsingTests(unittest.TestCase):
    def test_layers_follow_added_attribute_count(self):
        records = parse_image_annotations("sample.jpg", annotations_with_overlap()["sample.jpg"])
        layers = group_records_by_layer(records)
        self.assertEqual(sorted(layers), [1, 2])
        self.assertEqual(len(layers[1]), 1)
        self.assertEqual(len(layers[2]), 2)

    def test_overlapping_points_belong_to_every_subset(self):
        records = parse_image_annotations("sample.jpg", annotations_with_overlap()["sample.jpg"])
        # 渲染时每一层单独统计，父 caption 与子集不在同一张图上比较。
        layer_two = group_records_by_layer(records)[2]
        memberships = point_memberships(layer_two)
        self.assertEqual(len(memberships[(10.0, 10.0)]), 2)
        self.assertEqual(len(memberships[(40.0, 10.0)]), 1)
        summaries = subset_summaries(layer_two)
        self.assertIn("shared points: 1", summaries["pen red white cap"])
        self.assertIn("partial", summaries["pen red black band"])
        self.assertNotIn("partial", summaries["pen red white cap"])

    def test_invalid_points_are_rejected(self):
        broken = {"sample.jpg": {"red pen": {"class": "pen", "points": [["a", 1]]}}}
        with self.assertRaisesRegex(ValueError, "non-numeric"):
            parse_image_annotations("sample.jpg", broken["sample.jpg"])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        Image.new("RGB", (100, 100), "white").save(self.folder / "sample.jpg")

    def test_overlapping_subsets_render_markers_around_the_point(self):
        records = parse_image_annotations("sample.jpg", annotations_with_overlap()["sample.jpg"])
        with Image.open(self.folder / "sample.jpg") as image:
            figure = render_layer(image, "sample.jpg", records[1:], 2)
        self.assertGreater(figure.width, 100)
        colors = {figure.getpixel((x, y))
                  for x in range(figure.width) for y in range(figure.height)}
        used = {color for color in colors if color in PALETTE}
        # 共享点附近应出现两个子集各自的完整标记，以及标记组的浅色外圈。
        self.assertEqual(len(used), 2)
        self.assertIn(GROUP_RING, colors)

    def test_single_membership_has_no_group_ring(self):
        records = parse_image_annotations("sample.jpg", annotations_with_overlap()["sample.jpg"])
        with Image.open(self.folder / "sample.jpg") as image:
            figure = render_layer(image, "sample.jpg", [records[0]], 1)
        colors = {figure.getpixel((x, y))
                  for x in range(figure.width) for y in range(figure.height)}
        self.assertNotIn(GROUP_RING, colors)

    def test_generate_visualizations_skips_missing_images(self):
        annotations = annotations_with_overlap()
        annotations["missing.jpg"] = annotations["sample.jpg"]
        annotations_path = self.folder / "subsets.json"
        annotations_path.write_text(json.dumps(annotations), encoding="utf-8")
        generated = generate_visualizations(annotations_path, self.folder,
                                            self.folder / "out")
        self.assertEqual([path.name for path in generated],
                         ["sample_layer1.png", "sample_layer2.png"])
        with Image.open(generated[-1]) as figure:
            self.assertTrue(math.isfinite(float(figure.width)))


if __name__ == "__main__":
    unittest.main()
