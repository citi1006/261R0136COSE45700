from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.action_cleanliness import (
    ACTION_DECISION_CLEANED,
    ACTION_DECISION_IN_USE,
    ACTION_DECISION_NEEDS_CHECK,
    ACTION_DECISION_UNKNOWN,
    ActionCleanlinessService,
    parse_action_time_value,
    parse_trajectory_json,
)
from app.roi_store import ConfigStore
from app.schemas import ROI
from scripts.generate_test_data import generate_test_data


class ActionCleanlinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        generate_test_data()
        cls.store = ConfigStore()
        cls.roi = ROI.from_dict(
            {
                "name": "TABLE_1",
                "points": [
                    {"x": 160, "y": 220},
                    {"x": 320, "y": 220},
                    {"x": 320, "y": 360},
                    {"x": 160, "y": 360},
                ],
            }
        )

    def test_detects_likely_cleaned_from_dwell_time_only(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 200, "y": 250, "staff_id": "a"},
                    {"t": 20, "x": 210, "y": 255, "staff_id": "a"},
                    {"t": 45, "x": 220, "y": 260, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(self.roi, points)

        self.assertEqual(result.decision, ACTION_DECISION_CLEANED)
        self.assertEqual(result.score, 4)
        self.assertGreaterEqual(float(result.features["dwell_seconds"]), 35.0)

    def test_flags_needs_check_when_dwell_time_is_short(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 200, "y": 250, "staff_id": "a"},
                    {"t": 10, "x": 210, "y": 255, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=40).evaluate(self.roi, points)

        self.assertEqual(result.decision, ACTION_DECISION_NEEDS_CHECK)
        self.assertEqual(result.score, 2)
        self.assertLess(float(result.features["dwell_seconds"]), 15.0)

    def test_ignores_coverage_when_dwell_time_is_middle_band(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 120, "y": 260, "staff_id": "a"},
                    {"t": 10, "x": 315, "y": 260, "staff_id": "a"},
                    {"t": 20, "x": 315, "y": 340, "staff_id": "a"},
                    {"t": 30, "x": 150, "y": 340, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(self.roi, points)

        self.assertEqual(result.decision, ACTION_DECISION_UNKNOWN)
        self.assertEqual(result.score, 3)
        self.assertGreaterEqual(float(result.features["coverage_ratio"]), 0.5)
        self.assertGreaterEqual(float(result.features["dwell_seconds"]), 15.0)
        self.assertLess(float(result.features["dwell_seconds"]), 35.0)

    def test_scores_max_for_long_dwell_time(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 200, "y": 250, "staff_id": "a"},
                    {"t": 25, "x": 210, "y": 255, "staff_id": "a"},
                    {"t": 55, "x": 220, "y": 260, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(self.roi, points)

        self.assertEqual(result.decision, ACTION_DECISION_CLEANED)
        self.assertEqual(result.score, 5)

    def test_excludes_order_to_payment_window_from_dwell_score(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 200, "y": 250, "staff_id": "a"},
                    {"t": 20, "x": 210, "y": 255, "staff_id": "a"},
                    {"t": 40, "x": 220, "y": 260, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(
            self.roi,
            points,
            order_time=0,
            payment_completed_time=30,
        )

        self.assertEqual(result.decision, ACTION_DECISION_NEEDS_CHECK)
        self.assertEqual(result.score, 2)
        self.assertEqual(float(result.features["dwell_seconds"]), 10.0)
        self.assertEqual(float(result.features["excluded_order_payment_seconds"]), 30.0)

    def test_marks_table_in_use_before_payment_completion(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": 0, "x": 200, "y": 250, "staff_id": "a"},
                    {"t": 20, "x": 210, "y": 255, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(
            self.roi,
            points,
            order_time=0,
            payment_completed_time=30,
        )

        self.assertEqual(result.decision, ACTION_DECISION_IN_USE)
        self.assertIsNone(result.score)
        self.assertEqual(result.to_dict()["score_label"], "사용중")

    def test_datetime_order_payment_and_trajectory_times_are_supported(self) -> None:
        points = parse_trajectory_json(
            json.dumps(
                [
                    {"t": "2026-05-21T12:00:00", "x": 200, "y": 250, "staff_id": "a"},
                    {"t": "2026-05-21T12:00:20", "x": 210, "y": 255, "staff_id": "a"},
                    {"t": "2026-05-21T12:00:40", "x": 220, "y": 260, "staff_id": "a"},
                ]
            )
        )

        result = ActionCleanlinessService(near_margin=80).evaluate(
            self.roi,
            points,
            order_time=parse_action_time_value("2026-05-21T12:00:00", "order_time"),
            payment_completed_time=parse_action_time_value("2026-05-21T12:00:30", "payment_completed_time"),
        )

        self.assertEqual(result.decision, ACTION_DECISION_NEEDS_CHECK)
        self.assertEqual(result.score, 2)
        self.assertEqual(float(result.features["dwell_seconds"]), 10.0)

    def test_action_cleanliness_page_and_submit(self) -> None:
        client = TestClient(main_module.app)

        get_response = client.get("/action-cleanliness")
        self.assertEqual(get_response.status_code, 200)
        self.assertIn("Action 기반 청결도", get_response.text)
        self.assertIn('name="order_time"', get_response.text)
        self.assertIn('placeholder="YYYY-MM-DDTHH:mm:ss"', get_response.text)

        response = client.post(
            "/action-cleanliness",
            data={
                "config_id": "StoreAlpha_FrontCam",
                "table_roi_index": "0",
                "order_time": "2026-05-21T12:00:00",
                "payment_completed_time": "2026-05-21T12:00:00",
                "near_margin": "80",
                "manual_confirmed": "true",
                "trajectory_json": json.dumps(
                    [
                        {"t": "2026-05-21T12:00:00", "x": 110, "y": 110, "staff_id": "a"},
                        {"t": "2026-05-21T12:00:10", "x": 120, "y": 120, "staff_id": "a"},
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(ACTION_DECISION_CLEANED, response.text)
        self.assertIn("Action 청결도 점수", response.text)
        self.assertIn("테이블 주변 체류", response.text)

    def test_action_cleanliness_route_displays_in_use_for_active_payment_window(self) -> None:
        client = TestClient(main_module.app)

        response = client.post(
            "/action-cleanliness",
            data={
                "config_id": "StoreAlpha_FrontCam",
                "table_roi_index": "0",
                "order_time": "2026-05-21T12:00:00",
                "payment_completed_time": "2026-05-21T12:00:30",
                "near_margin": "80",
                "trajectory_json": json.dumps(
                    [
                        {"t": "2026-05-21T12:00:00", "x": 110, "y": 110, "staff_id": "a"},
                        {"t": "2026-05-21T12:00:10", "x": 120, "y": 120, "staff_id": "a"},
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("사용중", response.text)
        self.assertIn("ROI #0", response.text)


if __name__ == "__main__":
    unittest.main()
