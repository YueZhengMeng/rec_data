import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from agents_local import LocalAttributesExtractor
from main_local import parse_attributes, process_local_batch
from prompts import build_extract_attributes_prompt


class LocalPromptTests(unittest.TestCase):
    def test_local_messages_use_current_category_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            Image.new("RGB", (32, 32), "white").save(Path(directory) / "sample.jpg")
            image = Image.new("RGB", (16, 16), "white")
            extractor = object.__new__(LocalAttributesExtractor)
            extractor.image_dir = directory
            extractor.cropper = SimpleNamespace(create_crops=lambda *args, **kwargs: SimpleNamespace(
                full_image=image, context_image=image, fine_image=image,
            ))
            for category in ("person", "car", "bird", "cup"):
                with self.subTest(category=category):
                    instance = {"image_name": "sample.jpg", "category": category,
                                "attributes": "known detail", "point": [8, 8]}
                    messages, images = extractor._build_message(instance)
                    self.assertEqual(len(images), 3)
                    self.assertEqual([part["type"] for part in messages[0]["content"]],
                                     ["image", "image", "image", "text"])
                    self.assertEqual(messages[0]["content"][-1]["text"],
                                     build_extract_attributes_prompt(category, "known detail"))
                    self.assertNotIn("<|>Category Guidance Here<|>",
                                     messages[0]["content"][-1]["text"])

    def test_parse_full_and_partial_format(self):
        attributes = {"appearance": ["dark coat"], "action": [],
                      "location": ["by the door"], "relation": []}
        for expected in (attributes, {"appearance": ["dark coat"]}, {"action": []}):
            raw = json.dumps(expected)
            for content in (raw, f"```json\n{raw}\n```"):
                with self.subTest(content=content):
                    parsed, error, _, _ = parse_attributes(content)
                    self.assertIsNone(error)
                    self.assertEqual(parsed, expected)

        for invalid in ('{}',
                        '{"appearance": [], "action": [], "location": [], "relation": [], "extra": []}',
                        '[{"appearance": []}]',
                        '{"appearance": "dark"}',
                        '{"appearance": [1]}'):
            with self.subTest(invalid=invalid):
                parsed, error, _, _ = parse_attributes(invalid)
                self.assertIsNone(parsed)
                self.assertIsNotNone(error)

    def test_success_saves_parsed_attributes(self):
        attributes = {"appearance": ["dark coat"]}
        extractor = SimpleNamespace(infer_batch=lambda instances: [
            {"instance_id": instance["instance_id"], "content": json.dumps(attributes)}
            for instance in instances
        ])
        result = process_local_batch([{"instance_id": 7}], extractor)[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attributes"], attributes)


if __name__ == "__main__":
    unittest.main()
