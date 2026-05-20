from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.analysis import read_image, write_image
from app.person_masking import PersonDetection, PersonMaskService


class FakeSegmenter:
    def __init__(self, mask: np.ndarray, detections: list[PersonDetection] | None = None) -> None:
        self.mask = mask
        self.detections = detections or []

    def segment_people(self, image: np.ndarray) -> tuple[np.ndarray, list[PersonDetection]]:
        _ = image
        return self.mask.copy(), list(self.detections)


class PersonMaskingTest(unittest.TestCase):
    def test_black_mask_is_applied_to_mask2former_person_region(self) -> None:
        image = np.full((120, 100, 3), 255, dtype=np.uint8)
        image[18:109, 34:67] = (80, 80, 80)
        mask = np.zeros((120, 100), dtype=np.uint8)
        mask[18:109, 34:67] = 255
        detection = PersonDetection(x=34, y=18, width=33, height=91, score=0.94, source="mask2former")
        service = PersonMaskService(FakeSegmenter(mask, [detection]))

        masked_image, output_mask, detections = service.apply_black_mask(image)

        self.assertEqual(detections, [detection])
        self.assertEqual(int(np.count_nonzero(output_mask)), int(np.count_nonzero(mask)))
        self.assertTrue(np.all(masked_image[60, 50] == 0))
        self.assertTrue(np.all(masked_image[5, 5] == 255))

    def test_image_without_person_mask_is_unchanged(self) -> None:
        image = np.full((60, 80, 3), 180, dtype=np.uint8)
        service = PersonMaskService(FakeSegmenter(np.zeros((60, 80), dtype=np.uint8)))

        masked_image, mask, detections = service.apply_black_mask(image)

        self.assertEqual(detections, [])
        self.assertEqual(int(np.count_nonzero(mask)), 0)
        np.testing.assert_array_equal(masked_image, image)

    def test_mask_image_file_writes_png_output(self) -> None:
        image = np.full((60, 80, 3), 210, dtype=np.uint8)
        mask = np.zeros((60, 80), dtype=np.uint8)
        mask[10:20, 30:40] = 255
        detection = PersonDetection(x=30, y=10, width=10, height=10, score=0.87, source="mask2former")
        service = PersonMaskService(FakeSegmenter(mask, [detection]))

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.png"
            write_image(source_path, image)

            result = service.mask_image_file(source_path, output_stem="unit_test_mask")

        self.assertEqual(result.person_count, 1)
        self.assertAlmostEqual(result.masked_pixel_ratio, 100 / (60 * 80))
        self.assertTrue(result.masked_path.exists())
        saved_image = read_image(result.masked_path)
        self.assertEqual(saved_image.shape, image.shape)
        self.assertTrue(np.all(saved_image[15, 35] == 0))
        result.masked_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
