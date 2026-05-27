from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import app.main as main_module
from app.cleanliness import CleanlinessResult, PROMPT_PROFILE_RESTAURANT
from app.database import (
    clear_cleanliness_results,
    fetch_cleanliness_results,
    fetch_cleanliness_store_summary,
    init_db,
    insert_cleanliness_result,
)
from app.schemas import CCTVConfig, ROI
from scripts.generate_test_data import generate_test_data


class CleanlinessReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        generate_test_data()

    def setUp(self) -> None:
        clear_cleanliness_results()

    def test_store_summary_groups_cleanliness_results_by_store(self) -> None:
        base_record = {
            "analyzed_at": datetime(2026, 5, 21, 12, 0).isoformat(timespec="minutes"),
            "cctv_id": "cam",
            "cctv_nickname": "Front",
            "roi_name": "TABLE_1",
            "mode": "hybrid",
            "score": 4,
            "confidence": 0.8,
            "final_stage": "object_based",
            "summary": "summary",
            "source_path": "",
            "crop_path": "",
            "exact_objects": "[]",
            "estimated_objects": "[]",
            "findings": "[]",
            "action_features": "{}",
        }
        insert_cleanliness_result({**base_record, "store_name": "StoreA", "decision": "cleaned_likely"})
        insert_cleanliness_result({**base_record, "store_name": "StoreA", "decision": "needs_check", "score": 2})
        insert_cleanliness_result({**base_record, "store_name": "StoreB", "decision": "unknown", "score": 3})

        summary = fetch_cleanliness_store_summary()
        store_a = next(row for row in summary if row["store_name"] == "StoreA")
        store_b = next(row for row in summary if row["store_name"] == "StoreB")

        self.assertEqual(store_a["total_count"], 2)
        self.assertEqual(store_a["cleaned_count"], 1)
        self.assertEqual(store_a["needs_check_count"], 1)
        self.assertEqual(store_b["unknown_count"], 1)

    def test_cleanliness_route_persists_record_for_reports(self) -> None:
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
                    score=2,
                    confidence=0.72,
                    summary="Table has visible trash.",
                    findings=["Visible trash on table"],
                    exact_objects=["table", "trash"],
                    estimated_objects=[],
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
                    files={"image_file": ("frame.png", handle, "image/png")},
                )

            self.assertEqual(response.status_code, 200)
            records = fetch_cleanliness_results({"store_name": "StoreAlpha"})
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["mode"], "object")
            self.assertEqual(records[0]["decision"], "needs_check")
            self.assertEqual(records[0]["score"], 2)
        finally:
            if fake_service.source_path is not None:
                fake_service.source_path.unlink(missing_ok=True)
            if fake_service.inspected_path is not None:
                fake_service.inspected_path.unlink(missing_ok=True)
            main_module.cleanliness_service = original_service

    def test_cleanliness_reports_page_renders_store_summary(self) -> None:
        insert_cleanliness_result(
            {
                "analyzed_at": datetime(2026, 5, 21, 12, 0).isoformat(timespec="minutes"),
                "store_name": "StoreA",
                "cctv_id": "cam",
                "cctv_nickname": "Front",
                "roi_name": "TABLE_1",
                "mode": "action",
                "decision": "cleaned_likely",
                "score": None,
                "confidence": 0.88,
                "final_stage": "action_only",
                "summary": "Staff stayed near the table.",
                "source_path": "",
                "crop_path": "",
                "exact_objects": "[]",
                "estimated_objects": "[]",
                "findings": "[]",
                "action_features": "{}",
            }
        )

        client = TestClient(main_module.app)
        response = client.get("/reports")

        self.assertEqual(response.status_code, 200)
        self.assertIn("매장별 청결도 요약", response.text)
        self.assertIn("StoreA", response.text)
        self.assertIn("cleaned_likely", response.text)

    def test_reports_page_reflects_cleanliness_records_with_cctv_filter(self) -> None:
        insert_cleanliness_result(
            {
                "analyzed_at": datetime(2026, 5, 27, 12, 0).isoformat(timespec="minutes"),
                "store_name": "StoreBeta",
                "cctv_id": "StoreBeta::RealCam",
                "cctv_nickname": "RealCam",
                "roi_name": "TABLE_1",
                "mode": "video",
                "decision": "unknown",
                "score": None,
                "confidence": 0.0,
                "final_stage": "queued",
                "summary": "Video analysis job accepted.",
                "source_path": "data/mobile_videos/queued.avi",
                "crop_path": "",
                "exact_objects": "[]",
                "estimated_objects": "[]",
                "findings": "[]",
                "action_features": "{}",
            }
        )

        client = TestClient(main_module.app)
        response = client.get("/reports", params={"cctv_id": "StoreBeta::RealCam"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("StoreBeta", response.text)
        self.assertIn("RealCam", response.text)
        self.assertIn("queued", response.text)
        self.assertIn("Video analysis job accepted.", response.text)

    def test_reports_page_includes_configured_store_without_results(self) -> None:
        original_store = main_module.config_store

        class FakeConfigStore:
            def list_configs(self) -> list[Any]:
                return [
                    CCTVConfig(
                        store_name="ConfiguredOnlyStore",
                        cctv_nickname="MountedPhone",
                        reference_image_path="reference_images/configured_only.png",
                        areas=[ROI.from_rectangle("TABLE_1", 10, 20, 100, 80)],
                    )
                ]

        try:
            main_module.config_store = FakeConfigStore()
            client = TestClient(main_module.app)
            response = client.get("/reports")

            self.assertEqual(response.status_code, 200)
            self.assertIn("ConfiguredOnlyStore", response.text)
            self.assertIn("ConfiguredOnlyStore::MountedPhone", response.text)
            self.assertIn("TABLE_1", response.text)
        finally:
            main_module.config_store = original_store

    def test_cleanliness_reports_url_redirects_to_reports(self) -> None:
        client = TestClient(main_module.app)
        response = client.get("/cleanliness-reports", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/reports")


if __name__ == "__main__":
    unittest.main()
