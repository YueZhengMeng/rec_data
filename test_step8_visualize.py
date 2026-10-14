"""Tests for step6 subset visualization (including shared instances)."""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from step8_visualize import YES, render_page, run


class SubsetVisualizationTests(unittest.TestCase):
    def test_shared_instance_is_visible_in_separate_subset_images(self):
        nodes = [
            {"instance_ids": [1, 2], "yes_points": [[20, 20], [80, 80]],
             "negative_instance_ids": [3], "unknown_instance_ids": [4],
             "predicate": {"display": "left"}},
            {"instance_ids": [1, 3], "yes_points": [[20, 20], [60, 60]],
             "predicate": {"display": "bright"}},
        ]
        image = Image.new("RGB", (100, 100), "white")
        first = render_page(image, "sample.jpg", "object", nodes[0], subset_index=1,
                            max_width=100, max_height=100, sidebar_width=350)
        second = render_page(image, "sample.jpg", "object", nodes[1], subset_index=2,
                             max_width=100, max_height=100, sidebar_width=350)
        # Both contain the shared point; the unique points do not leak into the other image.
        self.assertEqual(first.getpixel((40, 116)), YES)
        self.assertEqual(second.getpixel((40, 116)), YES)
        self.assertEqual(first.getpixel((100, 176)), YES)
        self.assertEqual(second.getpixel((80, 156)), YES)
        self.assertEqual(first.getpixel((80, 156)), (255, 255, 255))
        self.assertEqual(second.getpixel((100, 176)), (255, 255, 255))
        self.assertEqual(first.size, second.size)

    def test_reads_step6_roots_and_skips_complement_without_step1_file(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            Image.new("RGB", (100, 100), "white").save(work / "sample.png")
            root = {"node_type": "caption_root", "image_name": "sample.png",
                    "caption": "object", "instance_ids": [1, 2],
                    "points": [[20, 20], [80, 80]]}
            positive = {"node_type": "predicate_subset", "node_id": "pred_red",
                        "image_name": "sample.png", "caption": "object",
                        "instance_ids": [1], "negative_instance_ids": [2],
                        "predicate": {"display": "red", "polarity": "positive"}}
            complement = {**positive, "node_id": "pred_not_red", "instance_ids": [2],
                          "complement_of": "pred_red",
                          "predicate": {"display": "not red", "polarity": "negative"}}
            annotations = work / "subsets.jsonl"
            annotations.write_text("".join(json.dumps(node) + "\n"
                                           for node in [root, positive, complement]), encoding="utf-8")
            args = SimpleNamespace(annotations=annotations, image_names=None, image_dir=work,
                                   output_dir=work / "out",
                                   max_width=100, max_height=100, sidebar_width=350)
            with redirect_stdout(StringIO()):
                self.assertEqual(run(args), 0)
            self.assertEqual([file.name for file in args.output_dir.glob("*.png")],
                             ["sample__pred_red.png"])
            with Image.open(args.output_dir / "sample__pred_red.png") as figure:
                self.assertEqual(figure.getpixel((40, 116)), YES)
                self.assertEqual(figure.getpixel((100, 176)), (255, 255, 255))

    def test_each_selected_subset_gets_its_own_image(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            Image.new("RGB", (100, 850), "white").save(work / "tall.png")
            root = {"node_type": "caption_root", "image_name": "tall.png",
                    "caption": "objects", "instance_ids": [1], "points": [[20, 20]]}
            subsets = [{"node_type": "predicate_subset", "node_id": f"pred_{index}",
                        "image_name": "tall.png", "caption": "objects",
                        "instance_ids": [1],
                        "predicate": {"display": f"part {index}"}} for index in range(10)]
            annotations = work / "subsets.jsonl"
            annotations.write_text("".join(json.dumps(node) + "\n"
                                           for node in [root, *subsets]), encoding="utf-8")
            args = SimpleNamespace(annotations=annotations, image_names=None, image_dir=work,
                                   output_dir=work / "out",
                                   max_width=100, max_height=850, sidebar_width=350)
            with redirect_stdout(StringIO()):
                self.assertEqual(run(args), 0)
            images = sorted(args.output_dir.glob("*.png"))
            self.assertEqual([file.name for file in images],
                             [f"tall__pred_{index}.png" for index in range(10)])
            for file in images:
                with Image.open(file) as figure:
                    self.assertEqual(figure.getpixel((40, 116)), YES)
                    self.assertEqual(figure.height, 980)


if __name__ == "__main__":
    unittest.main()
