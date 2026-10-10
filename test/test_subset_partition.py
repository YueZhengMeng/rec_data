import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from old.process_student_output import normalize_and_deduplicate, normalize_phrase
from semantic_attributes import last_token_pool
from subset_partition import (
    build_subset_tree,
    build_annotation_image,
    cluster_conditions,
    group_caption_records,
    report_cross_caption_attribute_matches,
    deduplicate_instance_attributes,
    run_partition,
)


class TableEncoder:
    def __init__(self, vectors=None):
        self.vectors = vectors or {}

    def encode(self, phrases):
        return {phrase: tuple(self.vectors.get(phrase, [1.0, 0.0])) for phrase in phrases}


def record(instance_id, appearance=None, action=None, caption="red pen", point=None,
           original="red", image="image.jpg", category="pen", kind="color",
           image_idx=0, caption_idx=0, location=None, relation=None):
    return {
        "instance_id": instance_id,
        "image_name": image,
        "caption": caption,
        "category": category,
        "attributes": original,
        "type": kind,
        "image_idx": image_idx,
        "caption_idx": caption_idx,
        "point": point if point is not None else [instance_id + 0.25, instance_id + 0.5],
        "more_attributes": {
            "appearance": appearance or [],
            "action": action or [],
            "location": location or [],
            "relation": relation or [],
        },
    }


class SubsetPartitionTests(unittest.TestCase):
    def test_qwen_last_token_pool_supports_left_and_right_padding(self):
        import torch

        hidden = torch.tensor([
            [[1., 0.], [2., 0.], [3., 0.]],
            [[0., 1.], [0., 2.], [0., 3.]],
        ])
        left_mask = torch.tensor([[0, 1, 1], [0, 0, 1]])
        right_mask = torch.tensor([[1, 1, 0], [1, 0, 0]])
        self.assertTrue(torch.equal(last_token_pool(hidden, left_mask), hidden[:, -1]))
        self.assertTrue(torch.equal(last_token_pool(hidden, right_mask),
                                    torch.tensor([[2., 0.], [0., 1.]])))

        self.assertEqual(normalize_phrase("  Bright  RED, body! "), "bright red body")

    def test_preprocessing_normalizes_and_deduplicates_in_first_seen_order(self):
        values = ["  Ｂｒｉｇｈｔ—RED, body! ", "bright red body", "Next\tto car.", "next to car"]
        self.assertEqual(normalize_and_deduplicate(values), ["bright red body", "next to car"])

    def test_subset_clustering_uses_stored_attribute_strings_verbatim(self):
        class RecordingEncoder(TableEncoder):
            def encode(self, phrases):
                phrases = list(phrases)
                self.seen = phrases
                return super().encode(phrases)

        encoder = RecordingEncoder()
        rows = [record(0, appearance=[" Already Normalized! "]),
                record(1, appearance=["plain phrase"])]
        conditions = cluster_conditions(rows, encoder, 0.99)
        self.assertIn(" Already Normalized! ", encoder.seen)
        self.assertTrue(any(" Already Normalized! " in item.phrases for item in conditions))

    def test_cluster_conditions_allows_cross_field_merges(self):
        rows = [
            record(0, appearance=["bright red body"]),
            record(1, action=["bright red body"]),
            record(2, location=["near car"]),
        ]
        encoder = TableEncoder({
            "bright red body": [1.0, 0.0],
            "near car": [0.0, 1.0],
            "red": [0.0, 1.0],
        })
        conditions = cluster_conditions(rows, encoder, 0.9)
        merged = [condition for condition in conditions
                  if condition.field == "more_attributes" and
                  condition.phrases == ("bright red body",)]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_fields, ("action", "appearance"))
        self.assertEqual(merged[0].support, 2)

    def test_cluster_semantic_matches_and_protects_color_and_field(self):
        rows = [record(0, appearance=["dark red body"]),
                record(1, appearance=["red body"]),
                record(2, appearance=["blue body"], action=["red body"])]
        encoder = TableEncoder({
            "dark red body": [1.0, 0.0],
            "red body": [0.99, 0.01],
            "blue body": [0.99, 0.01],
        })
        conditions = cluster_conditions(rows, encoder, 0.9)
        appearance = [item for item in conditions if item.field == "more_attributes"]
        self.assertEqual(len(appearance), 2)  # distinct cross-field semantic conditions.
        self.assertEqual(sorted(item.support for item in appearance), [1, 3])

    def test_complete_link_clustering_prevents_similarity_chaining(self):
        rows = [record(index, appearance=[phrase]) for index, phrase in enumerate(("alpha", "beta", "gamma"))]
        encoder = TableEncoder({"alpha": [1, 0], "beta": [0.8, 0.6], "gamma": [0.28, 0.96]})
        clusters = cluster_conditions(rows, encoder, 0.75)
        phrase_clusters = [item.phrases for item in clusters if item.field == "more_attributes"]
        self.assertEqual(len(phrase_clusters), 2)

    def test_caption_root_and_intersections_preserve_original_points(self):
        rows = [
            record(10, appearance=["smooth surface"], action=["standing"], point=[1.1, 2.2]),
            record(11, appearance=["smooth surface"], action=["standing"], point=[1.1, 2.2]),
            record(12, appearance=["rough surface"], action=["moving"], point=[3.3, 4.4]),
            record(13, appearance=["other surface"], action=["walking"], caption="blue pen", original="blue"),
        ]
        encoder = TableEncoder({
            "smooth surface": [1, 0, 0], "rough surface": [0, 0, 1],
            "standing": [0, 1, 0], "moving": [0, 0, 1],
        })
        tree = build_subset_tree(rows[:3], encoder, min_subset_size=2,
                                 max_condition_depth=2, similarity_threshold=0.8)
        self.assertEqual(tree["nodes"][0]["instance_ids"], [10, 11, 12])
        self.assertEqual(tree["nodes"][0]["points"], [[1.1, 2.2], [1.1, 2.2], [3.3, 4.4]])
        subsets = tree["nodes"][1:]
        self.assertTrue(subsets)
        self.assertTrue(all(node["support_count"] >= 2 for node in subsets))
        self.assertTrue(all(node["instance_ids"] != [10, 11, 12] for node in subsets))
        self.assertTrue(all(len(node["conditions"]) == 1 for node in subsets))
        self.assertTrue(all(node["parent_ids"] == [tree["root_id"]] for node in subsets))

    def test_original_attribute_is_used_as_a_partition_condition(self):
        rows = [record(0, original="red"), record(1, original="red"),
                record(2, original="blue")]
        tree = build_subset_tree(rows, TableEncoder(), min_subset_size=2,
                                 max_condition_depth=1, similarity_threshold=0.8)
        self.assertEqual(len(tree["nodes"]), 2)
        condition = tree["nodes"][1]["conditions"][0]
        self.assertTrue(condition["field"].startswith("original_attribute:"))
        self.assertEqual(tree["nodes"][1]["instance_ids"], [0, 1])

    def test_instance_global_dedup_keeps_original_then_shortest_enhancement(self):
        item = record(
            7,
            appearance=["red body", "very long coat"],
            action=["red", "long"],
            original="red",
            location=["near car"],
        )
        encoder = TableEncoder({
            "red": [1, 0], "red body": [0.99, 0.01],
            "very long coat": [0, 1], "long": [0, 1],
            "near car": [0, 0, 1],
        })
        cleaned = deduplicate_instance_attributes(item, encoder, 0.9)
        self.assertEqual(cleaned, {
            "appearance": [], "action": ["long"],
            "location": ["near car"], "relation": [],
        })
        # 去重函数返回新属性结构，不改动调用方的原始记录。
        self.assertEqual(item["more_attributes"]["appearance"], ["red body", "very long coat"])

    def test_cross_caption_comparison_uses_image_and_caption_indices(self):
        rows = [
            record(1, appearance=["blue body"], original="red", image_idx=10, caption_idx=0),
            record(2, original="blue", image_idx=10, caption_idx=1),
            record(3, original="blue", image_idx=11, caption_idx=1),
        ]
        encoder = TableEncoder({"blue body": [1, 0], "blue": [1, 0], "red": [0, 1]})
        captured = StringIO()
        with redirect_stdout(captured):
            count = report_cross_caption_attribute_matches(rows, encoder, 0.95)
        output = captured.getvalue()
        self.assertEqual(count, 1)
        self.assertIn("image_idx=10", output)
        self.assertIn("caption_idx=0", output)
        self.assertIn("caption_idx=1", output)
        self.assertIn("cosine=1.0000", output)
        self.assertNotIn("instance_id=3", output)  # different image_idx is not compared.
        self.assertEqual(rows[0]["more_attributes"]["appearance"], ["blue body"])

    def test_same_caption_attributes_are_not_reported_as_cross_caption(self):
        rows = [
            record(1, appearance=["red body"], original="red", image_idx=2, caption_idx=4),
            record(2, original="red", image_idx=2, caption_idx=4),
        ]
        captured = StringIO()
        with redirect_stdout(captured):
            count = report_cross_caption_attribute_matches(rows, TableEncoder(), 0.9)
        self.assertEqual(count, 0)
        self.assertEqual(captured.getvalue(), "")

    def test_minimum_support_and_depth(self):
        rows = [record(0, appearance=["shared"], action=["active"]),
                record(1, appearance=["shared"], action=["active"]),
                record(2, appearance=["unique"], action=["quiet"])]
        tree = build_subset_tree(rows, TableEncoder(), min_subset_size=2,
                                 max_condition_depth=1, similarity_threshold=0.9)
        self.assertTrue(all(len(path) == 1 for node in tree["nodes"][1:]
                            for path in node["condition_paths"]))
        self.assertTrue(all(node["support_count"] >= 2 for node in tree["nodes"][1:]))

    def test_grouping_does_not_audit_noncontiguous_repeated_roots(self):
        rows = [record(1), record(2, caption="other"), record(3)]
        groups = list(group_caption_records(rows))
        self.assertEqual([[row["instance_id"] for row in group] for group in groups],
                         [[1], [2], [3]])

    def test_duplicate_coordinates_keep_distinct_instance_ids(self):
        rows = [record(1, appearance=["same"], point=[2, 3]),
                record(2, appearance=["same"], point=[2, 3])]
        tree = build_subset_tree(rows, TableEncoder(), min_subset_size=2)
        self.assertEqual(tree["nodes"][0]["instance_ids"], [1, 2])
        self.assertEqual(tree["nodes"][0]["points"], [[2, 3], [2, 3]])

    def test_grouping_enforces_caption_boundaries_and_contiguous_groups(self):
        groups = list(group_caption_records([record(1), record(2), record(3, caption="new caption")]))
        self.assertEqual([[row["instance_id"] for row in group] for group in groups], [[1, 2], [3]])

    def test_annotation_output_matches_annotations_format(self):
        rows = [record(1, appearance=["shared"], point=[1, 2]),
                record(2, appearance=["shared"], point=[3, 4]),
                record(3, appearance=["other"], point=[5, 6])]
        tree = build_subset_tree(rows, TableEncoder({"shared": [1, 0], "other": [0, 1]}), min_subset_size=2,
                                 max_condition_depth=1, similarity_threshold=0.9)
        image = build_annotation_image([rows], [tree])
        self.assertIn("red pen", image)
        self.assertEqual(image["red pen"]["points"], [[1, 2], [3, 4], [5, 6]])
        self.assertEqual(image["pen red shared"]["class"], "pen")
        self.assertEqual(image["pen red shared"]["attribute"], "red")
        self.assertEqual(image["pen red shared"]["more_attributes"], ["shared"])

        class CountingEncoder(TableEncoder):
            def __init__(self):
                super().__init__()
                self.calls = []

            def encode(self, phrases):
                phrases = list(phrases)
                self.calls.append(phrases)
                return super().encode(phrases)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "subsets.json"
            rows = [
                record(1, appearance=["first"], image_idx=0),
                record(2, appearance=["second"], image_idx=0),
                record(3, appearance=["third"], image_idx=1),
            ]
            source.write_text(json.dumps(rows), encoding="utf-8")
            encoder = CountingEncoder()
            run_partition(source, output, encoder, 0.8, 2, 2)
            self.assertEqual(len(encoder.calls), 2)
            self.assertEqual(sorted(set(encoder.calls[0] + encoder.calls[1])),
                             ["first", "red", "second", "third"])

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "subsets.json"
            source.write_text(json.dumps([record(1), record(2, appearance=["shared"])]), encoding="utf-8")
            stats = run_partition(source, output, TableEncoder(), 0.8, 2, 2)
            self.assertEqual(stats["instances"], 2)
            metadata_output = output.with_name(output.stem + "_metadata" + output.suffix)
            parsed = json.loads(metadata_output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["metadata"]["min_subset_size"], 2)
            self.assertEqual(parsed["stats"]["instances"], 2)
            output.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                run_partition(source, output, TableEncoder(), 0.8, 2, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
