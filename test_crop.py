import base64
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from agents import AttributesExtractor
from crop import AdaptiveCropper, CropConfig


def _instance(point, image_name="sample.jpg", category="person"):
    return {
        "image_name": image_name,
        "category": category,
        "point": list(point),
    }


class AdaptiveCropperTests(unittest.TestCase):
    def setUp(self):
        self.config = CropConfig(output_size=64)

    def test_sparse_group_uses_fixed_fallback(self):
        cropper = AdaptiveCropper.from_instances(
            [_instance((50, 50)), _instance((80, 50)), _instance((50, 80))],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (200, 200), "gray"),
            "sample.jpg",
            "person",
            (50, 50),
        )

        self.assertEqual(crops.metadata.scale_source, "sparse_fallback")
        self.assertEqual(crops.metadata.fine_side, 50)
        self.assertEqual(crops.metadata.context_side, 100)
        self.assertEqual(crops.fine_image.size, (64, 64))
        self.assertEqual(crops.context_image.size, (64, 64))

    def test_knn_scale_uses_median_and_lower_bound(self):
        points = [(100, 100), (102, 100), (104, 100), (106, 100), (108, 100), (110, 100)]
        cropper = AdaptiveCropper.from_instances(
            [_instance(point) for point in points],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (400, 400), "gray"),
            "sample.jpg",
            "person",
            (100, 100),
        )

        self.assertEqual(crops.metadata.scale_source, "knn")
        self.assertEqual(crops.metadata.neighbors_used, 5)
        self.assertAlmostEqual(crops.metadata.median_neighbor_distance, 6.0)
        self.assertEqual(crops.metadata.fine_side, 32)
        self.assertEqual(crops.metadata.context_side, 64)

    def test_exact_duplicates_do_not_become_zero_distance_neighbors(self):
        points = [(50, 50), (50, 50), (60, 50), (70, 50), (80, 50)]
        cropper = AdaptiveCropper.from_instances(
            [_instance(point) for point in points],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (200, 200), "gray"),
            "sample.jpg",
            "person",
            (50, 50),
        )

        self.assertEqual(cropper.instance_point_count, 5)
        self.assertEqual(cropper.unique_point_count, 4)
        self.assertEqual(cropper.duplicate_point_count, 1)
        self.assertEqual(crops.metadata.scale_source, "knn")
        self.assertGreater(crops.metadata.median_neighbor_distance, 0)

    def test_boundary_crop_keeps_size_and_reports_clamping(self):
        cropper = AdaptiveCropper.from_instances(
            [_instance((-4, 100))],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (100, 80), "gray"),
            "sample.jpg",
            "person",
            (-4, 100),
        )

        metadata = crops.metadata
        self.assertTrue(metadata.point_was_clamped)
        self.assertEqual(metadata.original_point, (-4.0, 100.0))
        self.assertEqual(metadata.clamped_point, (0.0, 79.0))
        self.assertEqual(metadata.fine_box.side, metadata.fine_side)
        self.assertEqual(metadata.context_box.side, metadata.context_side)
        self.assertEqual(metadata.context_box.left, 0)
        self.assertEqual(metadata.context_box.bottom, 80)
        self.assertLessEqual(metadata.context_box.left, metadata.clamped_point[0])
        self.assertGreater(metadata.context_box.right, metadata.clamped_point[0])
        self.assertLessEqual(metadata.context_box.top, metadata.clamped_point[1])
        self.assertGreater(metadata.context_box.bottom, metadata.clamped_point[1])

    def test_context_alone_is_marked_and_input_is_unchanged(self):
        image = Image.new("RGB", (200, 200), (128, 128, 128))
        original_bytes = image.tobytes()
        cropper = AdaptiveCropper.from_instances(
            [_instance((100, 100))],
            self.config,
        )
        crops = cropper.create_crops(image, "sample.jpg", "person", (100, 100))
        plain_output = Image.new("RGB", (64, 64), (128, 128, 128))

        self.assertEqual(image.tobytes(), original_bytes)
        self.assertIsNone(ImageChops.difference(crops.fine_image, plain_output).getbbox())
        self.assertIsNotNone(ImageChops.difference(crops.context_image, plain_output).getbbox())
        marker_x, marker_y = crops.metadata.context_marker_point
        self.assertTrue(0 <= marker_x < 64)
        self.assertTrue(0 <= marker_y < 64)
        self.assertEqual(crops.context_image.getpixel((marker_x, marker_y)), (128, 128, 128))

    def test_centered_boxes_are_nested_and_square(self):
        points = [(150, 100), (180, 100), (210, 100), (240, 100)]
        cropper = AdaptiveCropper.from_instances(
            [_instance(point) for point in points],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (300, 200), "gray"),
            "sample.jpg",
            "person",
            (150, 100),
        )

        fine_box = crops.metadata.fine_box
        context_box = crops.metadata.context_box
        self.assertEqual(fine_box.width, fine_box.height)
        self.assertEqual(context_box.width, context_box.height)
        self.assertLessEqual(context_box.left, fine_box.left)
        self.assertLessEqual(context_box.top, fine_box.top)
        self.assertGreaterEqual(context_box.right, fine_box.right)
        self.assertGreaterEqual(context_box.bottom, fine_box.bottom)

    def test_missing_target_uses_explicit_fallback(self):
        cropper = AdaptiveCropper.from_instances(
            [_instance((10, 10)), _instance((20, 20)), _instance((30, 30)), _instance((40, 40))],
            self.config,
        )
        crops = cropper.create_crops(
            Image.new("RGB", (200, 200), "gray"),
            "sample.jpg",
            "person",
            (100, 100),
        )

        self.assertEqual(crops.metadata.scale_source, "missing_target_fallback")
        self.assertEqual(crops.metadata.fine_side, 50)


class AttributesExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_global_context_fine_without_debug_files(self):
        config = CropConfig(output_size=64)
        points = [(60, 50), (70, 50), (80, 50), (90, 50)]
        cropper = AdaptiveCropper.from_instances(
            [_instance(point) for point in points],
            config,
        )

        captured_messages = None

        async def fake_complete(messages, client, model_name):
            nonlocal captured_messages
            captured_messages = messages
            messages.append({
                "role": "assistant",
                "content": '{"appearance": [], "action": [], "location": [], "relation": []}',
                "think": "",
                "input_tokens": 0,
                "output_tokens": 0,
            })
            return messages

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "sample.jpg")
            Image.new("RGB", (120, 100), (128, 128, 128)).save(image_path)
            extractor = AttributesExtractor(
                "test",
                "test-api-key",
                "https://example.com/v1",
                "test-model",
                temp_dir,
                cropper,
            )

            old_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("agents.openai_complete_async", new=fake_complete):
                    content, _ = await extractor.extract_attributes({
                        "image_name": "sample.jpg",
                        "category": "person",
                        "attributes": "known",
                        "point": [60, 50],
                    })
            finally:
                os.chdir(old_cwd)

            self.assertIsNotNone(content)
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "global_image.jpg")))
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "local_image.jpg")))

        self.assertIsNotNone(captured_messages)
        content_blocks = captured_messages[0]["content"]
        image_blocks = [block for block in content_blocks if block["type"] == "image_url"]
        self.assertEqual(len(image_blocks), 3)

        decoded_images = []
        for block in image_blocks:
            encoded = block["image_url"]["url"].split(",", 1)[1]
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                decoded_images.append(image.convert("RGB").copy())

        self.assertEqual(decoded_images[0].size, (120, 100))
        self.assertEqual(decoded_images[1].size, (64, 64))
        self.assertEqual(decoded_images[2].size, (64, 64))
        self.assertIsNotNone(
            ImageChops.difference(decoded_images[1], decoded_images[2]).getbbox()
        )
        prompt = next(block["text"] for block in content_blocks if block["type"] == "text")
        self.assertIn("Image 2 (Context)", prompt)
        self.assertIn("Image 3 (Fine)", prompt)
        self.assertIn("not part of the scene", prompt)


if __name__ == "__main__":
    unittest.main()
