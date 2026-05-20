from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.analysis import AnalysisService, QualityResult, VideoValidator
from app.database import clear_results, fetch_results, init_db
from app.roi_store import ConfigStore
from app.schemas import Point, normalize_points_counterclockwise, normalize_points_to_rectangle
from scripts.generate_test_data import generate_test_data


class FakeVisibilityClient:
    def analyze_visibility(
        self,
        *,
        cropped_media_path: Path,
        media_type: str,
        roi_name: str,
        source_path: Path | None = None,
    ):
        _ = (cropped_media_path, media_type, roi_name)
        stem = source_path.stem if source_path is not None else ""
        if "occluded" in stem:
            return type("VisibilityAssessment", (), {"human_body_ratio": 0.72, "summary": "person blocks most of the ROI"})()
        return type("VisibilityAssessment", (), {"human_body_ratio": 0.04, "summary": "ROI is mostly clear of people"})()


class FakeQualityAnalyzer:
    def analyze_pop(
        self,
        *,
        media_path: Path,
        cropped_media_path: Path,
        media_type: str,
        roi,
        poster_template_path: Path,
        validation_result,
    ) -> QualityResult:
        _ = (cropped_media_path, media_type, roi, poster_template_path)
        if not validation_result.is_valid:
            return QualityResult(
                status="Unknown",
                confidence=0.0,
                summary=f"validator rejected {media_path.name}",
            )

        if "present" in media_path.stem:
            return QualityResult(status="Present", confidence=0.93, summary="fake openai found the poster")
        if "absent" in media_path.stem:
            return QualityResult(status="Absent", confidence=0.89, summary="fake openai did not find the poster")
        return QualityResult(status="Unknown", confidence=0.41, summary="fake openai was uncertain")


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        clear_results()
        generate_test_data()
        with Path("data/test_data/labels.json").open("r", encoding="utf-8") as handle:
            cls.labels = json.load(handle)
        cls.analysis_service = AnalysisService(
            validator=VideoValidator(visibility_client=FakeVisibilityClient()),
            quality_analyzer=FakeQualityAnalyzer(),
        )
        cls.store = ConfigStore()
        cls.generated_results = []
        for case in cls.labels["cases"]:
            config = cls.store.load(case["config_id"])
            roi = cls.store.get_roi(case["config_id"], case["roi_name"])
            result = cls.analysis_service.analyze_media(
                config=config,
                roi=roi,
                media_path=Path(case["media_path"]),
                poster_template_path=Path(cls.labels["poster_template_path"]),
                sensor_brightness=case["sensor_brightness"],
                enable_sensor_match=case["enable_sensor_match"],
            )
            cls.generated_results.append((case, result))

    def test_labeled_cases(self) -> None:
        for case, result in self.generated_results:
            self.assertEqual(result["decision"], case["expected_decision"], case["name"])
            self.assertEqual(result["validator"].is_valid, case["expected_valid"], case["name"])

    def test_records_are_persisted(self) -> None:
        rows = fetch_results()
        self.assertGreaterEqual(len(rows), 4)
        for row in rows:
            self.assertIn(row["decision"], {"Present", "Absent", "Unknown"})
            self.assertGreaterEqual(row["confidence"], 0.0)
            self.assertLessEqual(row["confidence"], 1.0)

    def test_image_analysis_includes_crop_preview(self) -> None:
        config = self.store.load("StoreAlpha_FrontCam")
        roi = self.store.get_roi(config.config_id, "POP")
        result = self.analysis_service.analyze_media(
            config=config,
            roi=roi,
            media_path=Path("data/test_data/StoreAlpha_FrontCam_present_frame.png"),
            poster_template_path=Path(self.labels["poster_template_path"]),
            enable_sensor_match=False,
        )
        self.assertEqual(result["decision"], "Present")
        self.assertIn("analysis_crop_url", result)
        self.assertTrue(result["analysis_crop_url"].startswith("/data/analysis_crops/"))
        self.assertTrue(Path(result["analysis_crop_path"]).exists())

    def test_store_catalog_and_rectangular_rois(self) -> None:
        store_names = self.store.list_store_names()
        self.assertIn("StoreAlpha", store_names)
        self.assertIn("StoreBeta", store_names)

        config = self.store.load("StoreAlpha_FrontCam")
        roi = self.store.get_roi(config.config_id, "POP")
        self.assertEqual(len(roi.points), 4)
        self.assertEqual(len({(point.x, point.y) for point in roi.points}), 4)
        bounds = roi.bounds
        self.assertEqual(
            roi.point_pairs(),
            [
                (bounds["x"], bounds["y"]),
                (bounds["x"] + bounds["width"], bounds["y"]),
                (bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]),
                (bounds["x"], bounds["y"] + bounds["height"]),
            ],
        )

    def test_point_order_is_normalized_counterclockwise(self) -> None:
        points = [
            Point(x=220, y=210),
            Point(x=120, y=80),
            Point(x=240, y=70),
            Point(x=90, y=190),
        ]
        normalized = normalize_points_counterclockwise(points)
        self.assertEqual(
            [(point.x, point.y) for point in normalized],
            [(90, 190), (220, 210), (240, 70), (120, 80)],
        )

    def test_points_can_be_normalized_to_rectangle(self) -> None:
        points = [
            Point(x=2, y=870),
            Point(x=270, y=875),
            Point(x=527, y=711),
            Point(x=281, y=703),
        ]
        normalized = normalize_points_to_rectangle(points)
        self.assertEqual(
            [(point.x, point.y) for point in normalized],
            [(2, 703), (527, 703), (527, 875), (2, 875)],
        )


if __name__ == "__main__":
    unittest.main()
