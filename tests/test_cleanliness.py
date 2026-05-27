from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np

import app.main as main_module
from app.analysis import read_image
from app.cleanliness import (
    CleanlinessAssessment,
    CleanlinessResult,
    CleanlinessService,
    PROMPT_PROFILE_GENERAL,
    PROMPT_PROFILE_RESTAURANT,
    build_cleanliness_analysis_prompt,
)


class FakeCleanlinessClient:
    def __init__(self, assessment: CleanlinessAssessment) -> None:
        self.assessment = assessment
        self.received_path: Path | None = None
        self.received_prompt_profile: str | None = None

    def analyze_cleanliness(self, image_path: Path, prompt_profile: str = PROMPT_PROFILE_RESTAURANT) -> CleanlinessAssessment:
        self.received_path = image_path
        self.received_prompt_profile = prompt_profile
        return self.assessment


class CleanlinessTest(unittest.TestCase):
    def test_inspect_image_sends_roi_crop_to_openai_client(self) -> None:
        assessment = CleanlinessAssessment(
            score=4,
            confidence=0.82,
            summary="The visible area is clean with only minor clutter.",
            findings=["Floor is clear", "Counters appear orderly"],
            exact_objects=["floor", "counter"],
            estimated_objects=["small scattered items"],
        )
        client = FakeCleanlinessClient(assessment)
        service = CleanlinessService(client)

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "store.png"
            crop_path = Path(temp_dir) / "roi_crop.png"
            source_path.write_bytes(b"not decoded by service")
            crop_path.write_bytes(b"not decoded by service")

            result = service.inspect_image(source_path, inspected_path=crop_path, prompt_profile=PROMPT_PROFILE_RESTAURANT)

        self.assertEqual(client.received_path, crop_path)
        self.assertEqual(client.received_prompt_profile, PROMPT_PROFILE_RESTAURANT)
        self.assertEqual(result.source_path, source_path)
        self.assertEqual(result.inspected_path, crop_path)
        self.assertEqual(result.prompt_profile, PROMPT_PROFILE_RESTAURANT)
        self.assertEqual(result.score, 4)
        self.assertEqual(result.confidence, 0.82)
        self.assertEqual(result.findings, ["Floor is clear", "Counters appear orderly"])
        self.assertEqual(result.exact_objects, ["floor", "counter"])
        self.assertEqual(result.estimated_objects, ["small scattered items"])

    def test_rejects_unsupported_file_type(self) -> None:
        service = CleanlinessService(
            FakeCleanlinessClient(
                CleanlinessAssessment(
                    score=5,
                    confidence=1.0,
                    summary="",
                    findings=[],
                    exact_objects=[],
                    estimated_objects=[],
                )
            )
        )

        with self.assertRaises(ValueError):
            service.inspect_image(Path("store.txt"))

    def test_cleanliness_route_uses_selected_roi_crop(self) -> None:
        class FakeCleanlinessService:
            def __init__(self) -> None:
                self.source_path: Path | None = None
                self.inspected_path: Path | None = None
                self.prompt_profile: str | None = None

            def inspect_image(
                self,
                source_path: Path,
                inspected_path: Path | None = None,
                prompt_profile: str = PROMPT_PROFILE_RESTAURANT,
            ) -> CleanlinessResult:
                self.source_path = source_path
                self.inspected_path = inspected_path
                self.prompt_profile = prompt_profile
                return CleanlinessResult(
                    source_path=source_path,
                    inspected_path=inspected_path or source_path,
                    score=3,
                    confidence=0.74,
                    summary="ROI has minor visible clutter.",
                    findings=["Some items are scattered on the surface"],
                    exact_objects=["table surface", "chair"],
                    estimated_objects=["small paper-like items"],
                    prompt_profile=prompt_profile,
                )

        fake_service = FakeCleanlinessService()
        original_service = main_module.cleanliness_service
        main_module.cleanliness_service = fake_service

        try:
            client = TestClient(main_module.app)
            image_path = Path("data/test_data/StoreAlpha_FrontCam_present_frame.png")
            with image_path.open("rb") as handle:
                response = client.post(
                    "/cleanliness",
                    data={
                        "config_id": "StoreAlpha_FrontCam",
                        "roi_name": "POP",
                        "prompt_profile": "restaurant",
                    },
                    files={"image_file": ("store.png", handle, "image/png")},
                )

            self.assertEqual(response.status_code, 200)
            self.assertIn("식당 특화 프롬프트", response.text)
            self.assertIn("OpenAI 입력 ROI crop", response.text)
            self.assertIn("정확히 식별 가능한 물체", response.text)
            self.assertIn("합리적으로 추정 가능한 물체", response.text)
            self.assertIn("table surface", response.text)
            self.assertIn("small paper-like items", response.text)
            self.assertIsNotNone(fake_service.source_path)
            self.assertIsNotNone(fake_service.inspected_path)
            self.assertEqual(fake_service.prompt_profile, PROMPT_PROFILE_RESTAURANT)
            assert fake_service.source_path is not None
            assert fake_service.inspected_path is not None
            self.assertTrue(fake_service.source_path.exists())
            self.assertTrue(fake_service.inspected_path.exists())
            self.assertNotEqual(fake_service.source_path, fake_service.inspected_path)
            self.assertEqual(fake_service.inspected_path.parent.name, "analysis_crops")
            roi_crop = read_image(fake_service.inspected_path)
            self.assertGreater(roi_crop.size, 0)
        finally:
            if fake_service.source_path is not None:
                fake_service.source_path.unlink(missing_ok=True)
            if fake_service.inspected_path is not None:
                fake_service.inspected_path.unlink(missing_ok=True)
            main_module.cleanliness_service = original_service

    def test_restaurant_prompt_contains_restaurant_context(self) -> None:
        prompt = build_cleanliness_analysis_prompt(PROMPT_PROFILE_RESTAURANT)

        self.assertIn("restaurant dining area", prompt)
        self.assertIn("tables", prompt)
        self.assertIn("dishes", prompt)
        self.assertIn("food residue", prompt)
        self.assertIn("exact_objects", prompt)
        self.assertIn("estimated_objects", prompt)

    def test_general_prompt_penalizes_wrappers_and_debris_on_surfaces(self) -> None:
        prompt = build_cleanliness_analysis_prompt(PROMPT_PROFILE_GENERAL)

        self.assertIn("do not use 3 when identifiable trash", prompt)
        self.assertIn("wrappers, snack packets, food packaging, or debris", prompt)
        self.assertIn("desk, table, counter, tray, or floor should normally score 2 or lower", prompt)


if __name__ == "__main__":
    unittest.main()
