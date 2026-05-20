from __future__ import annotations

import unittest

import numpy as np

from app.analysis import crop_roi, ordered_points
from app.schemas import Point, ROI


class RoiCropTest(unittest.TestCase):
    def test_trapezoid_points_are_coerced_to_rectangle(self) -> None:
        roi = ROI(
            name="table001",
            points=[
                Point(x=2, y=870),
                Point(x=270, y=875),
                Point(x=527, y=711),
                Point(x=281, y=703),
            ],
        )

        ordered = ordered_points(roi).astype(int).tolist()

        self.assertEqual(
            ordered,
            [
                [2, 703],
                [527, 703],
                [527, 875],
                [2, 875],
            ],
        )
        self.assertEqual(len({tuple(point) for point in ordered}), 4)

    def test_crop_roi_uses_rectangular_bounds(self) -> None:
        image = np.full((900, 600, 3), 255, dtype=np.uint8)
        roi = ROI(
            name="table001",
            points=[
                Point(x=2, y=870),
                Point(x=270, y=875),
                Point(x=527, y=711),
                Point(x=281, y=703),
            ],
        )

        crop = crop_roi(image, roi)

        self.assertEqual(crop.shape[:2], (172, 525))


if __name__ == "__main__":
    unittest.main()
