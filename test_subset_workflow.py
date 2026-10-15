"""Regression tests for the reordered step7/step8 subset workflow."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from recsub.subset_visualization import YES, load_subset_nodes, render_subset, render_subset_png
from step8_review_sheet import build_review_items


class SubsetWorkflowTests(unittest.TestCase):
    def make_annotations(self, folder: Path) -> Path:
        root = {
            "node_type": "caption_root", "image_name": "sample.png",
            "caption": "red pen", "category": "pen",
            "instance_ids": [1, 2], "points": [[20, 20], [80, 80]],
        }
        positive = {
            "node_type": "predicate_subset", "node_id": "pred_red",
            "image_name": "sample.png", "caption": "red pen", "category": "pen",
            "instance_ids": [1], "negative_instance_ids": [2], "unknown_instance_ids": [],
            "predicate": {"display": "in the bottom section", "dimension": "location",
                          "value": "bottom", "polarity": "positive", "source": "reused"},
        }
        complement = {
            **positive, "node_id": "pred_not_red", "instance_ids": [2],
            "complement_of": "pred_red",
            "predicate": {**positive["predicate"], "display": "not in the bottom section",
                          "polarity": "negative"},
        }
        path = folder / "subsets.jsonl"
        path.write_text("".join(json.dumps(row) + "\n"
                                       for row in (root, positive, complement)), encoding="utf-8")
        return path

    def test_loader_excludes_complement_and_resolves_root_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            subsets = self.make_annotations(Path(directory))
            nodes = load_subset_nodes(subsets)
            self.assertEqual([node["node_id"] for node in nodes], ["pred_red"])
            self.assertEqual(nodes[0]["yes_points"], [[20, 20]])
            self.assertEqual(nodes[0]["no_points"], [[80, 80]])

    def test_render_modes_share_dimensions_and_put_metadata_in_sidebar(self):
        node = {
            "node_id": "pred_red", "image_name": "sample.png", "caption": "red pen",
            "category": "pen", "instance_ids": [1], "yes_points": [[20, 20]],
            "no_points": [[80, 80]], "unknown_points": [],
            "predicate": {"display": "in the bottom section", "dimension": "location",
                          "value": "bottom", "source": "reused"},
        }
        source = Image.new("RGB", (100, 100), "white")
        positive = render_subset(source, node, mode="positive", max_width=100,
                                 max_height=100, sidebar_width=350)
        review = render_subset(source, node, mode="review", max_width=100,
                               max_height=100, sidebar_width=350)
        self.assertEqual(positive.size, review.size)
        self.assertEqual(positive.getpixel((40, 116)), YES)
        self.assertEqual(positive.getpixel((100, 176)), (255, 255, 255))
        self.assertEqual(review.getpixel((100, 176)), (205, 55, 50))
        self.assertGreater(positive.width, 100)
        self.assertGreater(positive.height, 100)

    def test_review_items_use_shared_nodes_and_png_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (100, 100), "white").save(folder / "sample.png")
            subsets = self.make_annotations(folder)
            items = build_review_items(subsets)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["category"], "pen")
            self.assertEqual(items[0]["caption"], "red pen")
            png = render_subset_png(folder / "sample.png", items[0], mode="review",
                                    max_width=100, max_height=100, sidebar_width=350)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
