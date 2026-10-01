import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


class OnlineAttributesTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_only_nonempty_subsets_of_allowed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            result_file = Path(directory) / "result.json"
            contents = (
                ('{"appearance": ["dark coat"]}', True),
                ('```json\n{"action": []}\n```', True),
                ('{"appearance": [], "action": [], "location": [], "relation": []}', True),
                ('{}', False),
                ('{"appearance": [], "extra": []}', False),
                ('{"appearance": "dark coat"}', False),
                ('{"appearance": [1]}', False),
                ('[{"appearance": []}]', False),
            )
            for content, expected_success in contents:
                with self.subTest(content=content):
                    result_file.write_text("[]", encoding="utf-8")

                    async def fake_extract(_instance):
                        return content, [{"role": "assistant", "think": "",
                                          "input_tokens": 1, "output_tokens": 2}]

                    with patch.object(main, "EXP_RESULT_FILE", str(result_file)), \
                            patch.object(main, "attributes_extractor",
                                         SimpleNamespace(extract_attributes=fake_extract)):
                        _, success = await main.extract_attributes({"instance_id": 7})
                    self.assertEqual(success, expected_success)
                    saved = json.loads(result_file.read_text(encoding="utf-8"))
                    self.assertEqual(len(saved), int(expected_success))
                    if expected_success:
                        self.assertEqual(saved[0]["attributes"],
                                         json.loads(content.replace("```json", "")
                                                    .replace("```", "").strip()))


if __name__ == "__main__":
    unittest.main()
