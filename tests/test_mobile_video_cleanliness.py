from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import app.main as main_module
from app.cleanliness import PROMPT_PROFILE_RESTAURANT
from app.database import clear_cleanliness_results, clear_owner_accounts, fetch_cleanliness_results, init_db
from app.roi_store import ConfigStore
from app.video_cleanliness import VideoCleanlinessAssessment, VideoCleanlinessResult, VideoCleanlinessService, parse_video_cleanliness_payload
from scripts.generate_test_data import generate_test_data


class FakeVideoCleanlinessService:
    def __init__(self) -> None:
        self.config_id: str | None = None
        self.roi_name: str | None = None
        self.video_path: Path | None = None
        self.prompt_profile: str | None = None
        self.device_id: str | None = None
        self.captured_at: str | None = None
        self.upload_period_seconds: float | None = None

    def inspect_video(
        self,
        *,
        config: Any,
        roi: Any,
        video_path: Path,
        prompt_profile: str = PROMPT_PROFILE_RESTAURANT,
        device_id: str = "",
        captured_at: str = "",
        upload_period_seconds: float | None = None,
    ) -> VideoCleanlinessResult:
        self.config_id = config.config_id
        self.roi_name = roi.name
        self.video_path = video_path
        self.prompt_profile = prompt_profile
        self.device_id = device_id
        self.captured_at = captured_at
        self.upload_period_seconds = upload_period_seconds
        return VideoCleanlinessResult(
            source_path=video_path,
            score=2,
            confidence=0.81,
            summary="Video shows visible trash on the table.",
            findings=["Trash remains visible after the observation window."],
            exact_objects=["table", "trash"],
            estimated_objects=["small paper-like debris"],
            prompt_profile=prompt_profile,
            device_id=device_id,
            captured_at=captured_at,
            upload_period_seconds=upload_period_seconds,
            analysis_url="https://analysis.example.test/cleanliness/video",
            raw_payload={"score": 2, "confidence": 0.81},
        )


class FailingVideoCleanlinessService:
    def __init__(self) -> None:
        self.video_path: Path | None = None

    def inspect_video(
        self,
        *,
        config: Any,
        roi: Any,
        video_path: Path,
        prompt_profile: str = PROMPT_PROFILE_RESTAURANT,
        device_id: str = "",
        captured_at: str = "",
        upload_period_seconds: float | None = None,
    ) -> VideoCleanlinessResult:
        self.video_path = video_path
        raise RuntimeError("analysis service unavailable")


class FakeOpenAIVideoClient:
    def __init__(self) -> None:
        self.contact_sheet_path: Path | None = None
        self.metadata: dict[str, Any] | None = None

    @property
    def analysis_url(self) -> str:
        return "openai://responses/test-model"

    def analyze_video(self, contact_sheet_path: Path, metadata: dict[str, Any]) -> VideoCleanlinessAssessment:
        self.contact_sheet_path = contact_sheet_path
        self.metadata = metadata
        self.assert_contact_sheet()
        return VideoCleanlinessAssessment(
            score=4,
            confidence=0.73,
            summary="Sampled ROI frames appear clean.",
            findings=["No visible trash in sampled frames."],
            exact_objects=["table"],
            estimated_objects=[],
            raw_payload={"score": 4, "confidence": 0.73},
        )

    def assert_contact_sheet(self) -> None:
        if self.contact_sheet_path is None:
            raise AssertionError("contact sheet path was not captured")
        if not self.contact_sheet_path.exists():
            raise AssertionError("contact sheet must exist while OpenAI client is called")
        if self.contact_sheet_path.suffix.lower() != ".png":
            raise AssertionError("contact sheet must be a PNG image")


class MobileVideoCleanlinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        generate_test_data()

    def setUp(self) -> None:
        clear_cleanliness_results()
        clear_owner_accounts()

    def auth_headers(self, client: TestClient) -> dict[str, str]:
        response = client.post(
            "/api/auth/bootstrap",
            data={"user_id": "owner", "password": "password123"},
        )
        self.assertEqual(response.status_code, 201)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_mobile_video_upload_returns_job_id_and_records_result_in_background(self) -> None:
        fake_service = FakeVideoCleanlinessService()
        original_service = main_module.video_cleanliness_service
        main_module.video_cleanliness_service = fake_service

        try:
            client = TestClient(main_module.app)
            headers = self.auth_headers(client)
            video_path = Path("data/test_data/storealpha_present.avi")
            with video_path.open("rb") as handle:
                response = client.post(
                    "/api/mobile/cleanliness-video",
                    headers=headers,
                    data={
                        "config_id": "StoreAlpha_FrontCam",
                        "roi_name": "POP",
                        "prompt_profile": "restaurant",
                        "device_id": "phone-01",
                        "captured_at": "2026-05-27T12:30:00+09:00",
                        "upload_period_seconds": "30",
                    },
                    files={"video_file": ("storealpha_present.avi", handle, "video/x-msvideo")},
                )

            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertIn("job_id", payload)
            self.assertNotIn("decision", payload)
            self.assertNotIn("score", payload)
            self.assertEqual(fake_service.config_id, "StoreAlpha_FrontCam")
            self.assertEqual(fake_service.roi_name, "POP")
            self.assertEqual(fake_service.prompt_profile, "restaurant")
            self.assertEqual(fake_service.device_id, "phone-01")
            self.assertEqual(fake_service.captured_at, "2026-05-27T12:30:00+09:00")
            self.assertEqual(fake_service.upload_period_seconds, 30.0)
            self.assertIsNotNone(fake_service.video_path)
            assert fake_service.video_path is not None
            self.assertTrue(fake_service.video_path.exists())
            self.assertEqual(fake_service.video_path.parent.name, "mobile_videos")

            records = fetch_cleanliness_results({"store_name": "StoreAlpha"})
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["mode"], "video")
            self.assertEqual(records[0]["decision"], "needs_check")
            self.assertEqual(records[0]["final_stage"], "video_external")
            action_features = json.loads(records[0]["action_features"])
            self.assertEqual(action_features["job_id"], payload["job_id"])
            self.assertEqual(action_features["device_id"], "phone-01")
            self.assertEqual(action_features["upload_period_seconds"], 30.0)
        finally:
            if fake_service.video_path is not None:
                fake_service.video_path.unlink(missing_ok=True)
            main_module.video_cleanliness_service = original_service

    def test_mobile_video_upload_records_failed_background_job(self) -> None:
        fake_service = FailingVideoCleanlinessService()
        original_service = main_module.video_cleanliness_service
        main_module.video_cleanliness_service = fake_service

        try:
            client = TestClient(main_module.app)
            headers = self.auth_headers(client)
            video_path = Path("data/test_data/storealpha_present.avi")
            with video_path.open("rb") as handle:
                response = client.post(
                    "/api/mobile/cleanliness-video",
                    headers=headers,
                    data={
                        "config_id": "StoreAlpha_FrontCam",
                        "roi_name": "POP",
                        "prompt_profile": "restaurant",
                        "device_id": "phone-02",
                        "captured_at": "2026-05-27T12:45:00+09:00",
                        "upload_period_seconds": "30",
                    },
                    files={"video_file": ("storealpha_present.avi", handle, "video/x-msvideo")},
                )

            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertIn("job_id", payload)
            self.assertNotIn("decision", payload)

            records = fetch_cleanliness_results({"store_name": "StoreAlpha"})
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["mode"], "video")
            self.assertEqual(records[0]["decision"], "unknown")
            self.assertEqual(records[0]["final_stage"], "video_failed")
            self.assertIn("analysis service unavailable", records[0]["summary"])
            action_features = json.loads(records[0]["action_features"])
            self.assertEqual(action_features["job_id"], payload["job_id"])
            self.assertEqual(action_features["device_id"], "phone-02")
            self.assertEqual(action_features["error"], "analysis service unavailable")
        finally:
            if fake_service.video_path is not None:
                fake_service.video_path.unlink(missing_ok=True)
            main_module.video_cleanliness_service = original_service

    def test_video_cleanliness_service_sends_roi_contact_sheet_to_openai_client(self) -> None:
        fake_client = FakeOpenAIVideoClient()
        service = VideoCleanlinessService(fake_client)
        config = main_module.config_store.load("StoreAlpha_FrontCam")
        roi = main_module.config_store.get_roi("StoreAlpha_FrontCam", "POP")

        result = service.inspect_video(
            config=config,
            roi=roi,
            video_path=Path("data/test_data/storealpha_present.avi"),
            prompt_profile="restaurant",
            device_id="phone-03",
            captured_at="2026-05-27T13:00:00+09:00",
            upload_period_seconds=30,
        )

        self.assertEqual(result.score, 4)
        self.assertEqual(result.analysis_url, "openai://responses/test-model")
        self.assertIsNotNone(fake_client.metadata)
        assert fake_client.metadata is not None
        self.assertEqual(fake_client.metadata["analysis_input"], "roi_contact_sheet")
        self.assertEqual(fake_client.metadata["config_id"], "StoreAlpha_FrontCam")
        self.assertEqual(fake_client.metadata["roi"]["name"], "POP")
        self.assertEqual(fake_client.metadata["device_id"], "phone-03")
        self.assertIsNotNone(fake_client.contact_sheet_path)
        assert fake_client.contact_sheet_path is not None
        self.assertFalse(fake_client.contact_sheet_path.exists())

    def test_video_analysis_payload_accepts_nested_cleanliness_score(self) -> None:
        assessment = parse_video_cleanliness_payload(
            {
                "result": {
                    "cleanliness_score": 4.6,
                    "cleanliness_confidence": 0.77,
                    "message": "The table is clean.",
                    "evidence": ["No visible trash"],
                }
            }
        )

        self.assertEqual(assessment.score, 5)
        self.assertEqual(assessment.confidence, 0.77)
        self.assertEqual(assessment.summary, "The table is clean.")
        self.assertEqual(assessment.findings, ["No visible trash"])

    def test_mobile_roi_config_save_creates_config_from_reference_frame(self) -> None:
        original_store = main_module.config_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "roi_configs").mkdir()
            (root / "reference_images").mkdir()
            main_module.config_store = ConfigStore(
                config_dir=root / "roi_configs",
                reference_dir=root / "reference_images",
                store_catalog_path=root / "stores.json",
            )

            try:
                client = TestClient(main_module.app)
                headers = self.auth_headers(client)
                image_path = Path("data/test_data/StoreAlpha_FrontCam_present_frame.png")
                with image_path.open("rb") as handle:
                    response = client.post(
                        "/api/mobile/roi-configs",
                        headers=headers,
                        data={
                            "store_name": "MobileStore",
                            "cctv_nickname": "MountedPhone",
                            "rois_json": json.dumps(
                                [
                                    {
                                        "name": "TABLE_1",
                                        "x": 10,
                                        "y": 20,
                                        "width": 120,
                                        "height": 80,
                                    }
                                ]
                            ),
                        },
                        files={"reference_image": ("reference.png", handle, "image/png")},
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["config_id"], "MobileStore_MountedPhone")
                self.assertEqual(payload["areas"][0]["name"], "TABLE_1")
                self.assertEqual(payload["areas"][0]["bounds"]["width"], 120)
                self.assertTrue((root / "roi_configs" / "MobileStore_MountedPhone.json").exists())
                self.assertTrue((root / "reference_images" / "MobileStore_MountedPhone.png").exists())

                with image_path.open("rb") as handle:
                    second_response = client.post(
                        "/api/mobile/roi-configs",
                        headers=headers,
                        data={
                            "store_name": "MobileStore",
                            "cctv_nickname": "MountedPhone",
                            "rois_json": json.dumps(
                                [
                                    {
                                        "name": "TABLE_2",
                                        "x": 160,
                                        "y": 40,
                                        "width": 90,
                                        "height": 70,
                                    }
                                ]
                            ),
                        },
                        files={"reference_image": ("reference.png", handle, "image/png")},
                    )

                self.assertEqual(second_response.status_code, 200)
                updated_payload = second_response.json()
                roi_names = [area["name"] for area in updated_payload["areas"]]
                self.assertEqual(roi_names, ["TABLE_1", "TABLE_2"])
                self.assertEqual(updated_payload["areas"][0]["bounds"]["width"], 120)
                self.assertEqual(updated_payload["areas"][1]["bounds"]["width"], 90)
            finally:
                main_module.config_store = original_store


if __name__ == "__main__":
    unittest.main()
