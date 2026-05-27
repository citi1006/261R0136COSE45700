from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.action_cleanliness import ACTION_DECISION_CLEANED, ActionCleanlinessResult
from app.cleanliness import CleanlinessResult, PROMPT_PROFILE_RESTAURANT
from app.hybrid_cleanliness import (
    HYBRID_STAGE_ACTION,
    HYBRID_STAGE_OBJECT,
    OBJECT_CONFIDENCE_THRESHOLD,
    build_hybrid_cleanliness_result,
)
from scripts.generate_test_data import generate_test_data


def fake_object_result(confidence: float, score: int = 4) -> CleanlinessResult:
    return CleanlinessResult(
        source_path=Path("source.png"),
        inspected_path=Path("crop.png"),
        score=score,
        confidence=confidence,
        summary="Object-based cleanliness summary.",
        findings=["Visible table surface"],
        exact_objects=["table"],
        estimated_objects=["paper-like item"],
        prompt_profile=PROMPT_PROFILE_RESTAURANT,
    )


def fake_action_result() -> ActionCleanlinessResult:
    return ActionCleanlinessResult(
        decision=ACTION_DECISION_CLEANED,
        score=4,
        confidence=0.88,
        summary="Staff trajectory covered the table.",
        evidence=["테이블 주변 체류 시간 44.0초"],
        features={
            "dwell_seconds": 44.0,
            "coverage_ratio": 0.75,
            "approach_count": 2,
            "visited_sides": ["left", "right", "bottom"],
        },
    )


class HybridCleanlinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        generate_test_data()

    def test_uses_object_result_when_confidence_is_high(self) -> None:
        result = build_hybrid_cleanliness_result(
            fake_object_result(confidence=0.82, score=4),
            None,
            object_confidence_threshold=OBJECT_CONFIDENCE_THRESHOLD,
        )

        self.assertEqual(result.final_stage, HYBRID_STAGE_OBJECT)
        self.assertFalse(result.fallback_triggered)
        self.assertEqual(result.final_decision, ACTION_DECISION_CLEANED)
        self.assertIsNone(result.action_result)

    def test_falls_back_to_action_when_object_confidence_is_low(self) -> None:
        action_result = fake_action_result()
        result = build_hybrid_cleanliness_result(
            fake_object_result(confidence=0.42, score=3),
            action_result,
            object_confidence_threshold=OBJECT_CONFIDENCE_THRESHOLD,
        )

        self.assertEqual(result.final_stage, HYBRID_STAGE_ACTION)
        self.assertTrue(result.fallback_triggered)
        self.assertEqual(result.final_decision, action_result.decision)
        self.assertEqual(result.action_result, action_result)

    def test_hybrid_route_runs_action_only_on_low_object_confidence(self) -> None:
        class FakeCleanlinessService:
            def __init__(self, confidence: float) -> None:
                self.confidence = confidence
                self.source_path: Path | None = None
                self.inspected_path: Path | None = None

            def inspect_image(
                self,
                source_path: Path,
                inspected_path: Path | None = None,
                prompt_profile: str = PROMPT_PROFILE_RESTAURANT,
            ) -> CleanlinessResult:
                self.source_path = source_path
                self.inspected_path = inspected_path
                return CleanlinessResult(
                    source_path=source_path,
                    inspected_path=inspected_path or source_path,
                    score=3,
                    confidence=self.confidence,
                    summary="Low confidence object result.",
                    findings=["Object identity is unclear"],
                    exact_objects=[],
                    estimated_objects=["paper-like item"],
                    prompt_profile=prompt_profile,
                )

        class FakeActionService:
            def __init__(self) -> None:
                self.called = False

            def evaluate(self, roi, trajectory_points, *, near_margin=None, manual_confirmed=False):
                self.called = True
                return fake_action_result()

        fake_cleanliness = FakeCleanlinessService(confidence=0.41)
        fake_action = FakeActionService()
        original_cleanliness = main_module.cleanliness_service
        original_action = main_module.action_cleanliness_service
        main_module.cleanliness_service = fake_cleanliness
        main_module.action_cleanliness_service = fake_action

        try:
            client = TestClient(main_module.app)
            image_path = Path("data/test_data/StoreAlpha_FrontCam_present_frame.png")
            with image_path.open("rb") as handle:
                response = client.post(
                    "/hybrid-cleanliness",
                    data={
                        "config_id": "StoreAlpha_FrontCam",
                        "roi_name": "POP",
                        "prompt_profile": "restaurant",
                        "near_margin": "80",
                        "manual_confirmed": "false",
                        "trajectory_json": json.dumps(
                            [
                                {"t": 0, "x": 100, "y": 100, "staff_id": "a"},
                                {"t": 10, "x": 130, "y": 120, "staff_id": "a"},
                            ]
                        ),
                    },
                    files={"image_file": ("frame.png", handle, "image/png")},
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(fake_action.called)
            self.assertIn(HYBRID_STAGE_ACTION, response.text)
            self.assertIn("Action fallback 평가", response.text)
            self.assertIn(ACTION_DECISION_CLEANED, response.text)
        finally:
            if fake_cleanliness.source_path is not None:
                fake_cleanliness.source_path.unlink(missing_ok=True)
            if fake_cleanliness.inspected_path is not None:
                fake_cleanliness.inspected_path.unlink(missing_ok=True)
            main_module.cleanliness_service = original_cleanliness
            main_module.action_cleanliness_service = original_action

    def test_hybrid_route_skips_action_when_object_confidence_is_high(self) -> None:
        class FakeCleanlinessService:
            def __init__(self) -> None:
                self.source_path: Path | None = None
                self.inspected_path: Path | None = None

            def inspect_image(
                self,
                source_path: Path,
                inspected_path: Path | None = None,
                prompt_profile: str = PROMPT_PROFILE_RESTAURANT,
            ) -> CleanlinessResult:
                self.source_path = source_path
                self.inspected_path = inspected_path
                return CleanlinessResult(
                    source_path=source_path,
                    inspected_path=inspected_path or source_path,
                    score=5,
                    confidence=0.9,
                    summary="High confidence clean table.",
                    findings=["Table is clear"],
                    exact_objects=["table"],
                    estimated_objects=[],
                    prompt_profile=prompt_profile,
                )

        class FakeActionService:
            def __init__(self) -> None:
                self.called = False

            def evaluate(self, roi, trajectory_points, *, near_margin=None, manual_confirmed=False):
                self.called = True
                return fake_action_result()

        fake_cleanliness = FakeCleanlinessService()
        fake_action = FakeActionService()
        original_cleanliness = main_module.cleanliness_service
        original_action = main_module.action_cleanliness_service
        main_module.cleanliness_service = fake_cleanliness
        main_module.action_cleanliness_service = fake_action

        try:
            client = TestClient(main_module.app)
            image_path = Path("data/test_data/StoreAlpha_FrontCam_present_frame.png")
            with image_path.open("rb") as handle:
                response = client.post(
                    "/hybrid-cleanliness",
                    data={
                        "config_id": "StoreAlpha_FrontCam",
                        "roi_name": "POP",
                        "prompt_profile": "restaurant",
                        "near_margin": "80",
                        "trajectory_json": "[]",
                    },
                    files={"image_file": ("frame.png", handle, "image/png")},
                )

            self.assertEqual(response.status_code, 200)
            self.assertFalse(fake_action.called)
            self.assertIn(HYBRID_STAGE_OBJECT, response.text)
            self.assertIn("미실행", response.text)
        finally:
            if fake_cleanliness.source_path is not None:
                fake_cleanliness.source_path.unlink(missing_ok=True)
            if fake_cleanliness.inspected_path is not None:
                fake_cleanliness.inspected_path.unlink(missing_ok=True)
            main_module.cleanliness_service = original_cleanliness
            main_module.action_cleanliness_service = original_action


if __name__ == "__main__":
    unittest.main()
