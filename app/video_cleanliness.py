from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI

from app.analysis import OpenAIModelClient, build_cropped_video_contact_sheet_file
from app.cleanliness import (
    CLEANLINESS_ANALYSIS_SCHEMA,
    DEFAULT_CLEANLINESS_PROMPT_PROFILE,
    build_cleanliness_analysis_prompt,
    normalize_cleanliness_prompt_profile,
)
from app.config import OPENAI_MODEL
from app.schemas import CCTVConfig, ROI


VIDEO_CLEANLINESS_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}


@dataclass(frozen=True)
class VideoCleanlinessAssessment:
    score: int
    confidence: float
    summary: str
    findings: list[str]
    exact_objects: list[str]
    estimated_objects: list[str]
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class VideoCleanlinessResult:
    source_path: Path
    score: int
    confidence: float
    summary: str
    findings: list[str]
    exact_objects: list[str]
    estimated_objects: list[str]
    prompt_profile: str
    device_id: str
    captured_at: str
    upload_period_seconds: float | None
    analysis_url: str
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "score": self.score,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "findings": self.findings,
            "exact_objects": self.exact_objects,
            "estimated_objects": self.estimated_objects,
            "prompt_profile": self.prompt_profile,
            "device_id": self.device_id,
            "captured_at": self.captured_at,
            "upload_period_seconds": self.upload_period_seconds,
            "analysis_url": self.analysis_url,
            "raw_payload": self.raw_payload,
        }


class VideoCleanlinessClient(Protocol):
    def analyze_video(self, contact_sheet_path: Path, metadata: dict[str, Any]) -> VideoCleanlinessAssessment:
        ...

    @property
    def analysis_url(self) -> str:
        ...


class OpenAIVideoCleanlinessClient(OpenAIModelClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = OPENAI_MODEL,
        client: OpenAI | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model, client=client)

    @property
    def analysis_url(self) -> str:
        return f"openai://responses/{self.model}"

    def analyze_video(self, contact_sheet_path: Path, metadata: dict[str, Any]) -> VideoCleanlinessAssessment:
        prompt_profile = str(metadata.get("prompt_profile") or DEFAULT_CLEANLINESS_PROMPT_PROFILE)
        prompt = (
            build_cleanliness_analysis_prompt(prompt_profile)
            + "\n\n"
            "The image input is a chronological contact sheet of cropped ROI frames sampled from one uploaded video.\n"
            "Evaluate cleanliness across the sampled frames as one short observation window.\n"
            "If an issue appears in any sampled frame, include it in findings and reflect it in the score.\n"
            "Use the metadata only to understand store, CCTV, ROI, and capture timing; do not invent visual evidence from metadata.\n"
            f"Metadata JSON: {json.dumps(metadata, ensure_ascii=False)}"
        )
        payload = self.create_json_response(
            prompt=prompt,
            image_paths=[contact_sheet_path],
            schema_name="video_cleanliness_analysis",
            schema=CLEANLINESS_ANALYSIS_SCHEMA,
            temperature=0.1,
        )
        return parse_video_cleanliness_payload(payload)


def parse_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def score_from_decision(decision: str | None) -> int | None:
    if not decision:
        return None
    normalized = decision.lower()
    if normalized in {"clean", "cleaned", "cleaned_likely", "ok", "pass"}:
        return 4
    if normalized in {"dirty", "needs_check", "fail", "unclean"}:
        return 2
    if normalized in {"unknown", "uncertain"}:
        return 3
    return None


def parse_video_cleanliness_payload(payload: dict[str, Any]) -> VideoCleanlinessAssessment:
    result_payload = payload.get("result", payload)
    if not isinstance(result_payload, dict):
        raise RuntimeError("video analysis result must be a JSON object.")

    score_value = first_present(result_payload, ("score", "cleanliness_score"))
    if score_value is None:
        score_value = score_from_decision(str(result_payload.get("decision", "")))
    if score_value is None:
        raise RuntimeError("video analysis response must include score, cleanliness_score, or decision.")

    score = max(1, min(5, int(round(float(score_value)))))
    confidence_value = first_present(result_payload, ("confidence", "cleanliness_confidence"))
    confidence = 0.0 if confidence_value is None else float(confidence_value)
    confidence = max(0.0, min(1.0, confidence))
    summary = str(first_present(result_payload, ("summary", "message", "description")) or "")

    return VideoCleanlinessAssessment(
        score=score,
        confidence=confidence,
        summary=summary,
        findings=parse_string_list(first_present(result_payload, ("findings", "evidence"))),
        exact_objects=parse_string_list(result_payload.get("exact_objects")),
        estimated_objects=parse_string_list(result_payload.get("estimated_objects")),
        raw_payload=payload,
    )


class VideoCleanlinessService:
    def __init__(self, client: VideoCleanlinessClient | None = None) -> None:
        self.client = client or OpenAIVideoCleanlinessClient()

    def inspect_video(
        self,
        *,
        config: CCTVConfig,
        roi: ROI,
        video_path: Path,
        prompt_profile: str = DEFAULT_CLEANLINESS_PROMPT_PROFILE,
        device_id: str = "",
        captured_at: str = "",
        upload_period_seconds: float | None = None,
    ) -> VideoCleanlinessResult:
        if video_path.suffix.lower() not in VIDEO_CLEANLINESS_EXTENSIONS:
            raise ValueError("video file must be mp4, mov, m4v, avi, webm, or mkv")

        normalized_prompt_profile = normalize_cleanliness_prompt_profile(prompt_profile)
        metadata = {
            "config_id": config.config_id,
            "store_name": config.store_name,
            "cctv_id": config.cctv_id,
            "cctv_nickname": config.cctv_nickname,
            "roi": roi.to_dict(),
            "analysis_input": "roi_contact_sheet",
            "prompt_profile": normalized_prompt_profile,
            "device_id": device_id,
            "captured_at": captured_at,
            "upload_period_seconds": upload_period_seconds,
        }
        contact_sheet_path = build_cropped_video_contact_sheet_file(video_path, roi)
        try:
            assessment = self.client.analyze_video(contact_sheet_path, metadata)
        finally:
            contact_sheet_path.unlink(missing_ok=True)
        return VideoCleanlinessResult(
            source_path=video_path,
            score=assessment.score,
            confidence=assessment.confidence,
            summary=assessment.summary,
            findings=assessment.findings,
            exact_objects=assessment.exact_objects,
            estimated_objects=assessment.estimated_objects,
            prompt_profile=normalized_prompt_profile,
            device_id=device_id,
            captured_at=captured_at,
            upload_period_seconds=upload_period_seconds,
            analysis_url=self.client.analysis_url,
            raw_payload=assessment.raw_payload,
        )
