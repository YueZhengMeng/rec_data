"""检查按 caption 候选整理和逐实例三值判断的核心数据逻辑。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from agents import PredicateJudge
from crop import AdaptiveCropper, CropConfig
from contextlib import redirect_stdout
from io import StringIO
from judge_predicates import (build_work_list, judge_one, parse_labels,
                              phrases_for_instance, run as run_judge)
from subset_partition import attach_judgments, build_subset_tree, main as partition_main
from propose_predicates import (build_llm_items, build_proposal_messages,
                                collect_caption_phrases, group_by_caption, propose_one, run as run_propose)
from semantic_attributes import deduplicate_phrases


class FakeEncoder:
    def encode(self, phrases):
        vectors = {
            "red": (1, 0), "bright red": (0.99, 0.01),
            "black": (0, 1), "white": (0, 1),
            "left": (0.7, 0.7), "right": (0.7, 0.7),
        }
        return {phrase: vectors.get(phrase, (0.5, 0.5)) for phrase in phrases}


class CandidateTests(unittest.TestCase):
    def test_same_image_captions_are_separate_roots(self):
        rows = [
            {"image_name": "image.jpg", "caption_idx": 0, "caption": "red pen",
             "category": "pen", "attributes": "red",
             "more_attributes": {"appearance": ["white cap"]}},
            {"image_name": "image.jpg", "caption_idx": 1, "caption": "blue pen",
             "category": "pen", "attributes": "blue",
             "more_attributes": {"appearance": ["black band"]}},
        ]
        groups = group_by_caption(rows)
        self.assertEqual(list(groups), [("image.jpg", 0), ("image.jpg", 1)])
        self.assertEqual([row["phrase"] for row in collect_caption_phrases(groups[("image.jpg", 0)])],
                         ["red", "white cap"])
        self.assertEqual([row["phrase"] for row in collect_caption_phrases(groups[("image.jpg", 1)])],
                         ["blue", "black band"])

    def test_embedding_prefers_rec_attribute_and_shortest(self):
        candidates = [
            {"phrase": "bright red", "category": "pen", "original": False},
            {"phrase": "red", "category": "pen", "original": True},
            {"phrase": "white", "category": "pen", "original": False},
            {"phrase": "black", "category": "pen", "original": False},
            {"phrase": "left", "category": "pen", "original": False},
            {"phrase": "right", "category": "pen", "original": False},
        ]
        unique = deduplicate_phrases(candidates, FakeEncoder(), 0.86)
        texts = {item["phrase"] for item in unique}
        self.assertIn("red", texts)
        self.assertNotIn("bright red", texts)
        self.assertIn("white", texts)
        self.assertIn("black", texts)
        self.assertIn("left", texts)
        self.assertIn("right", texts)
        self.assertFalse(any(x["phrase"] == "red" for x in build_llm_items(
            candidates, FakeEncoder(), 0.86, 3)))

    def test_originals_are_used_for_dedup_but_not_sent(self):
        records = [{"category": "pen", "attributes": "red",
                    "more_attributes": {"appearance": ["red", "bright red", "white"]}}]
        phrases = collect_caption_phrases(records)
        items = build_llm_items(phrases, FakeEncoder(), 0.86, 80, 4)
        self.assertEqual([item["phrase"] for item in items], ["white"])
        self.assertEqual(json.loads(build_proposal_messages("pen", items)[0]["content"].split("Additional attributes: ")[-1]),
                         ["white"])

    def test_length_and_word_filters_run_before_embedding_deduplication(self):
        phrases = [{"phrase": "one two three four five", "category": "pen", "original": False},
                   {"phrase": "red body", "category": "pen", "original": False}]
        result = build_llm_items(phrases, FakeEncoder(), 0.86, max_length=80, max_words=4)
        self.assertEqual([item["phrase"] for item in result], ["red body"])

        items = [{"phrase": "white laces", "category": "shoe", "original": False}]
        messages = build_proposal_messages("shoe", items)
        text = messages[0]["content"]
        self.assertIn('Category: shoe', text)
        self.assertIn('["white laces"]', text)
        self.assertNotIn("image.jpg", text)
        self.assertNotIn("original", text)


class OnlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_per_image_and_filters_original_words(self):
        items = [{"phrase": "white laces", "category": "shoe", "original": False},
                 {"phrase": "in a row", "category": "shoe", "original": False}]
        called = []

        async def fake_complete(messages, client, model):
            called.append(messages)
            return messages + [{"content": '["white laces", "in a row"]'}]

        with patch("propose_predicates.openai_complete_async", fake_complete):
            result = await propose_one("image.jpg", "shoe", "shoe", items,
                                       {"in a row"}, None, "test")
        self.assertEqual(result, ["white laces"])
        self.assertEqual(len(called), 1)

    async def test_model_cannot_invent_new_attributes(self):
        items = [{"phrase": "white laces", "category": "shoe", "original": False}]

        async def fake_complete(messages, client, model):
            return messages + [{"content": '["invented", "white laces"]'}]

        with patch("propose_predicates.openai_complete_async", fake_complete):
            result = await propose_one("image.jpg", "shoe", "shoe", items, set(), None, "test")
        self.assertEqual(result, ["white laces"])

    async def test_run_writes_nested_caption_lists_and_resumes_individually(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "extract.json"
            output = Path(directory) / "candidates.json"
            source.write_text(json.dumps([
                {"image_name": "a.jpg", "caption_idx": 0, "caption": "red pen", "category": "pen",
                 "attributes": "red", "more_attributes": {"appearance": ["white"]}},
                {"image_name": "a.jpg", "caption_idx": 1, "caption": "blue pen", "category": "pen",
                 "attributes": "blue", "more_attributes": {"appearance": ["black"]}},
            ]), encoding="utf-8")
            args = SimpleNamespace(input=source, output=output, max_images=0,
                                   embedding_model=Path(directory), embedding_batch_size=2,
                                   device=None, base_url="https://example.org/v1", model="test",
                                   batch_size=2, similarity_threshold=0.86,
                                   max_length=80, max_words=4)
            calls = []

            async def fake_complete(messages, client, model):
                payload = json.loads(messages[0]["content"].split("Additional attributes: ")[-1])
                calls.append(payload)
                return messages + [{"content": json.dumps(payload)}]

            with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test"}), \
                    patch("propose_predicates.Qwen3EmbeddingEncoder", return_value=FakeEncoder()), \
                    patch("propose_predicates.AsyncOpenAI"), \
                    patch("propose_predicates.openai_complete_async", fake_complete):
                await run_propose(args)
                await run_propose(args)
            self.assertEqual(calls, [["white"], ["black"]])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             {"a.jpg": {"red pen": ["white"], "blue pen": ["black"]}})

    async def test_judge_run_only_saves_instance_lists_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            instances_file = directory / "instances.json"
            candidates_file = directory / "candidates.json"
            output_file = directory / "results.json"
            instances = [{"instance_id": i, "image_name": "a.jpg", "caption": "red pen",
                          "category": "pen", "attributes": "red", "point": [i, i],
                          "image_idx": 0, "caption_idx": 0, "point_idx": i}
                         for i in range(2)]
            instances_file.write_text(json.dumps(instances), encoding="utf-8")
            candidates_file.write_text(json.dumps({"a.jpg": {"red pen": ["white cap", "black band"]}}),
                                       encoding="utf-8")
            args = SimpleNamespace(instances=instances_file, candidates=candidates_file,
                                   output=output_file, max_images=0, image_dir=str(directory),
                                   base_url="https://example.org/v1", model="test", batch_size=2)
            calls = []

            class FakeJudge:
                def __init__(self, *args):
                    pass

                async def judge(self, instance, phrases):
                    calls.append(instance["instance_id"])
                    if instance["instance_id"] == 0:
                        return '{"labels":{"white cap":"yes","black band":"no"}}', {}
                    return '{"labels":{"white cap":"unknown","black band":"yes"}}', {}

            with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test"}), \
                    patch("judge_predicates.PredicateJudge", FakeJudge), \
                    patch("judge_predicates.AdaptiveCropper"):
                await run_judge(args)
                await run_judge(args)
            self.assertEqual(calls, [0, 1])
            results = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertNotIn("labels", results[0])
            self.assertEqual(results[0]["yes"], ["white cap"])
            self.assertEqual(results[0]["no"], ["black band"])
            self.assertEqual(results[1]["unknown"], ["white cap"])
            self.assertEqual(results[0]["caption_idx"], 0)
            self.assertEqual({p.name for p in directory.glob("*.json")},
                             {"instances.json", "candidates.json", "results.json"})

    async def test_failed_judgment_and_empty_candidates_keep_three_lists(self):
        instance = {"instance_id": 5, "caption": "pen", "image_name": "a.jpg"}
        class FailingJudge:
            async def judge(self, instance, phrases):
                raise RuntimeError("test failure")
        failed = await judge_one(FailingJudge(), instance, ["white cap"])
        self.assertEqual(failed["unknown"], ["white cap"])
        self.assertEqual(failed["yes"], [])
        empty = await judge_one(FailingJudge(), instance, [])
        self.assertEqual([empty[key] for key in ("yes", "no", "unknown")], [[], [], []])

    async def test_three_views_and_original_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            row = {"image_name": "sample.jpg", "caption": "black shoe", "category": "shoe",
                   "point": [30, 30], "instance_id": 0}
            Image.new("RGB", (64, 64), "white").save(Path(directory) / "sample.jpg")
            cropper = AdaptiveCropper.from_instances([row], CropConfig(output_size=32))

            async def fake_complete(messages, client, model):
                content = messages[0]["content"]
                self.assertEqual(sum(item["type"] == "image_url" for item in content), 3)
                self.assertIn("black shoe", content[-1]["text"])
                self.assertIn("white laces", content[-1]["text"])
                return messages + [{"content": '{"labels":{"white laces":"unknown"}}'}]

            with patch("agents.openai_complete_async", fake_complete):
                judge = PredicateJudge("test", "unused", "https://example.org/v1",
                                       "test", directory, cropper)
                content, _ = await judge.judge(row, ["white laces"])
            self.assertEqual(parse_labels(content, ["white laces"]),
                             {"yes": [], "no": [], "unknown": ["white laces"]})


class SubsetTests(unittest.TestCase):
    def test_work_list_is_flat_and_ignores_unselected_captions(self):
        instances = []
        for index, (image_name, caption) in enumerate([("a.jpg", "pen"), ("a.jpg", "cup"),
                                                       ("b.jpg", "pen"), ("c.jpg", "pen")]):
            instances.append({"instance_id": index, "image_name": image_name,
                              "caption": caption, "category": "pen", "point": [index, 0]})
        candidates = {"a.jpg": {"pen": ["white"]}, "b.jpg": {"pen": ["black"]},
                      "c.jpg": {"pen": ["small"]}}
        work = build_work_list(instances, candidates, completed=set(), max_images=0)
        # 不同图片的实例在同一个列表中，未入选的 caption 不产生任务。
        self.assertEqual([row[0]["instance_id"] for row in work], [0, 2, 3])
        self.assertEqual([row[1] for row in work], [["white"], ["black"], ["small"]])
        limited = build_work_list(instances, candidates, completed={0}, max_images=2)
        self.assertEqual([row[0]["instance_id"] for row in limited], [2])

    def test_judging_uses_only_the_current_caption(self):
        candidates = {"image.jpg": {"red pen": ["white cap"],
                                    "blue pen": ["black band"]}}
        self.assertEqual(phrases_for_instance(candidates,
                         {"image_name": "image.jpg", "caption": "red pen"}), ["white cap"])
        self.assertEqual(phrases_for_instance(candidates,
                         {"image_name": "image.jpg", "caption": "blue pen"}), ["black band"])
        self.assertEqual(phrases_for_instance(candidates,
                         {"image_name": "image.jpg", "caption": "green pen"}), [])

    def test_cross_caption_repeats_are_printed_without_filtering(self):
        candidates = {"image.jpg": {"red pen": ["white cap", "blue"],
                                    "blue pen": ["white cap"]}}
        output = StringIO()
        with redirect_stdout(output):
            phrases = phrases_for_instance(candidates,
                       {"image_name": "image.jpg", "caption": "red pen"})
        self.assertEqual(phrases, ["white cap", "blue"])
        self.assertIn("跨caption属性重复", output.getvalue())
        self.assertIn("white cap", output.getvalue())
        self.assertIn("blue pen", output.getvalue())

    def test_partition_uses_only_yes_and_keeps_unknown_out(self):
        instances = [{"instance_id": i, "image_name": "a.jpg", "caption": "shoe",
                      "category": "shoe", "attributes": "", "type": "other",
                      "image_idx": 0, "caption_idx": 0, "point": [i, i]}
                     for i in range(3)]
        results = [{**instances[0], "yes": ["white laces"], "no": [], "unknown": []},
                   {**instances[1], "yes": [], "no": ["white laces"], "unknown": []},
                   {**instances[2], "yes": [], "no": [], "unknown": ["white laces"]}]
        records = attach_judgments(instances, results)
        tree = build_subset_tree(records, None, min_subset_size=1)
        self.assertEqual(tree["nodes"][0]["instance_ids"], [0, 1, 2])
        self.assertEqual(tree["nodes"][1]["instance_ids"], [0])
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "instances.json").write_text(json.dumps(instances), encoding="utf-8")
            (directory / "labels.json").write_text(json.dumps(results), encoding="utf-8")
            # 新入口只读判断结果，不加载 embedding 模型。
            status = partition_main([
                "--input", str(directory / "labels.json"),
                "--instances", str(directory / "instances.json"),
                "--output", str(directory / "subsets.json"), "--min-subset-size", "1"
            ])
            self.assertEqual(status, 0)

            output = json.loads((directory / "subsets.json").read_text(encoding="utf-8"))["a.jpg"]
        self.assertEqual(output["shoe white laces"]["points"], [[0, 0]])
        self.assertFalse(output["shoe white laces"]["complete"])
        self.assertEqual(output["shoe"]["points"], [[0, 0], [1, 1], [2, 2]])

    def test_overlapping_subsets_are_all_kept(self):
        instances = [{"instance_id": i, "image_name": "a.jpg", "caption": "pen",
                      "category": "pen", "attributes": "red", "type": "color",
                      "image_idx": 0, "caption_idx": 0, "point": [i, i]} for i in range(4)]
        results = []
        for index, yes in enumerate([["white", "black"], ["white", "black"],
                                     ["white"], ["black"]]):
            results.append({**instances[index], "yes": yes, "no": [], "unknown": []})
        tree = build_subset_tree(attach_judgments(instances, results), None,
                                 min_subset_size=1, max_condition_depth=2)
        subsets = {node["conditions"][0]["canonical_phrase"]: set(node["instance_ids"])
                   for node in tree["nodes"][1:] if len(node["conditions"]) == 1}
        self.assertEqual(subsets["white"], {0, 1, 2})
        self.assertEqual(subsets["black"], {0, 1, 3})
        # 交叉子集与它的两个父条件同时存在，子集之间允许重叠。
        intersections = [set(node["instance_ids"]) for node in tree["nodes"]
                         if len(node["conditions"]) == 2]
        self.assertEqual(intersections, [{0, 1}])
        ids = {node["node_id"] for node in tree["nodes"]}
        self.assertTrue(all(parent in ids for node in tree["nodes"] for parent in node["parent_ids"]))

    def test_missing_judgment_keeps_full_parent_and_yes_intersections(self):
        instances = [{"instance_id": i, "image_name": "a.jpg", "caption": "red pen",
                      "category": "pen", "attributes": "red", "image_idx": 0,
                      "caption_idx": 0, "type": "color", "point": [i, i]}
                     for i in range(5)]
        results = [{**instances[0], "yes": ["white cap", "black band"], "no": [], "unknown": []},
                   {**instances[1], "yes": ["black band"], "no": ["white cap"], "unknown": []},
                   {**instances[2], "yes": ["black band"], "no": ["white cap"], "unknown": []},
                   {**instances[3], "yes": ["white cap"], "no": ["black band"], "unknown": []}]
        joined = attach_judgments(instances, results)
        self.assertEqual(len(joined), 5)
        self.assertEqual(joined[-1]["yes"], [])
        tree = build_subset_tree(joined, None, min_subset_size=1, max_condition_depth=2)
        self.assertEqual(tree["nodes"][0]["instance_ids"], [0, 1, 2, 3, 4])
        intersections = [node for node in tree["nodes"] if len(node["conditions"]) == 2]
        self.assertEqual(len(intersections), 1)
        self.assertEqual(intersections[0]["instance_ids"], [0])

    def test_malformed_and_omitted_labels_are_unknown(self):
        expected = {"yes": [], "no": [], "unknown": ["red"]}
        self.assertEqual(parse_labels("garbled", ["red"]), expected)
        self.assertEqual(parse_labels('{"labels":{}}', ["red"]), expected)

    def test_three_lists_preserve_each_candidate(self):
        result = parse_labels('{"labels":{"red":"yes","blue":"no","black":"invalid"}}',
                              ["red", "blue", "black", "white"])
        self.assertEqual(result, {"yes": ["red"], "no": ["blue"], "unknown": ["black", "white"]})



if __name__ == "__main__":
    unittest.main()
