from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI

from app.analysis import OpenAIModelClient
from app.config import OPENAI_MODEL


CLEANLINESS_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
PROMPT_PROFILE_GENERAL = "general"
PROMPT_PROFILE_RESTAURANT = "restaurant"
DEFAULT_CLEANLINESS_PROMPT_PROFILE = PROMPT_PROFILE_RESTAURANT


CLEANLINESS_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Cleanliness score from 1 to 5. 1 is very dirty and 5 is very clean.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence in the cleanliness assessment.",
        },
        "summary": {
            "type": "string",
            "description": "Short explanation grounded in visible cleanliness evidence.",
        },
        "findings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Visible evidence that influenced the cleanliness score.",
        },
        "exact_objects": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Objects that are directly and clearly identifiable from visible evidence in the ROI crop.",
        },
        "estimated_objects": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Objects that are reasonably inferable by a human observer despite low resolution, blur, occlusion, or partial visibility.",
        },
    },
    "required": ["score", "confidence", "summary", "findings", "exact_objects", "estimated_objects"],
    "propertyOrdering": ["score", "confidence", "summary", "findings", "exact_objects", "estimated_objects"],
}


GENERAL_CLEANLINESS_CONTEXT = (
    "You are inspecting a cropped store or facility ROI for visible cleanliness.\n"
)

RESTAURANT_CLEANLINESS_CONTEXT = (
    "You are inspecting a cropped ROI from a restaurant dining area for table and surrounding-area cleanliness.\n"
    "The ROI may include tables, chairs, benches, floor, walls, trays, dishes, bowls, plates, cups, bottles, napkins, utensils, food residue, spills, crumbs, trash, and cleaning-related evidence.\n"
    "Use restaurant dining context when deciding whether partially visible objects are likely restaurant objects, but still distinguish exact identification from reasonable estimation.\n"
)

BASE_CLEANLINESS_ANALYSIS_PROMPT = (
    "Do not create, restore, upscale, enhance, or generate a new image.\n"
    "Analyze only the provided ROI crop as-is.\n"
    "\n"
    "First extract visible objects from the ROI crop.\n"
    "Separate objects into two lists:\n"
    "- exact_objects: objects that are directly identifiable from visible evidence.\n"
    "- estimated_objects: objects that are not perfectly clear but are reasonably inferable by a human observer from shape, location, context, or partial visibility.\n"
    "Do not give up only because the image is low-resolution, blurry, or partially visible.\n"
    "Record observations up to the level a careful human observer could reasonably estimate.\n"
    "Do not invent hidden, cropped-out, or fully invisible objects.\n"
    "\n"
    "Rate the cleanliness on a strict 1 to 5 scale:\n"
    "1 = very dirty, severe trash, spills, grime, or sanitation concern.\n"
    "2 = dirty, visible trash, wrappers, snack packets, food packaging, debris, residue, stains, spills, or clutter that requires cleaning.\n"
    "3 = borderline acceptable, with only light non-trash issues such as slight disorder, faint dust, or a single ambiguous speck; do not use 3 when identifiable trash, wrappers, snack packets, food packaging, or debris is visible.\n"
    "4 = clean with only incidental non-trash issues.\n"
    "5 = very clean, orderly, and no visible cleanliness problems.\n"
    "Be strict about surfaces where people work, eat, or place items: visible wrappers, snack packets, plastic packaging, crumbs, food waste, or small debris on a desk, table, counter, tray, or floor should normally score 2 or lower.\n"
    "Only give 3 when the ROI would plausibly be acceptable without immediate cleaning.\n"
    "Judge cleanliness only from visible or reasonably estimated evidence in the ROI crop, such as trash, stains, spills, dust, food waste, clutter, floor/counter condition, and general orderliness.\n"
    "If an observation is estimated rather than certain, say so clearly in estimated_objects or findings.\n"
    "If the image is dark, blurry, or partially blocked, lower confidence and explain the limitation, but still record reasonable human-observable details.\n"
    "Return only JSON."
)


def normalize_cleanliness_prompt_profile(prompt_profile: str | None) -> str:
    if prompt_profile == PROMPT_PROFILE_GENERAL:
        return PROMPT_PROFILE_GENERAL
    return PROMPT_PROFILE_RESTAURANT


def build_cleanliness_analysis_prompt(prompt_profile: str = DEFAULT_CLEANLINESS_PROMPT_PROFILE) -> str:
    normalized = normalize_cleanliness_prompt_profile(prompt_profile)
    context = RESTAURANT_CLEANLINESS_CONTEXT if normalized == PROMPT_PROFILE_RESTAURANT else GENERAL_CLEANLINESS_CONTEXT
    return context + BASE_CLEANLINESS_ANALYSIS_PROMPT


@dataclass(frozen=True)
class CleanlinessAssessment:
    score: int
    confidence: float
    summary: str
    findings: list[str]
    exact_objects: list[str]
    estimated_objects: list[str]


@dataclass(frozen=True)
class CleanlinessResult:
    source_path: Path
    inspected_path: Path
    score: int
    confidence: float
    summary: str
    findings: list[str]
    exact_objects: list[str]
    estimated_objects: list[str]
    prompt_profile: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "inspected_path": str(self.inspected_path),
            "score": self.score,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "findings": self.findings,
            "exact_objects": self.exact_objects,
            "estimated_objects": self.estimated_objects,
            "prompt_profile": self.prompt_profile,
        }


class CleanlinessClient(Protocol):
    def analyze_cleanliness(
        self,
        image_path: Path,
        prompt_profile: str = DEFAULT_CLEANLINESS_PROMPT_PROFILE,
    ) -> CleanlinessAssessment:
        ...


class OpenAICleanlinessClient(OpenAIModelClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = OPENAI_MODEL,
        client: OpenAI | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model, client=client)

    def analyze_cleanliness(
        self,
        image_path: Path,
        prompt_profile: str = DEFAULT_CLEANLINESS_PROMPT_PROFILE,
    ) -> CleanlinessAssessment:
        payload = self.create_json_response(
            prompt=build_cleanliness_analysis_prompt(prompt_profile),
            image_paths=[image_path],
            schema_name="cleanliness_analysis",
            schema=CLEANLINESS_ANALYSIS_SCHEMA,
            temperature=0.1,
        )

        score = int(payload["score"])
        confidence = float(payload["confidence"])
        findings = [str(item) for item in payload.get("findings", [])]
        exact_objects = [str(item) for item in payload.get("exact_objects", [])]
        estimated_objects = [str(item) for item in payload.get("estimated_objects", [])]
        return CleanlinessAssessment(
            score=max(1, min(5, score)),
            confidence=max(0.0, min(1.0, confidence)),
            summary=str(payload["summary"]),
            findings=findings,
            exact_objects=exact_objects,
            estimated_objects=estimated_objects,
        )


class CleanlinessService:
    def __init__(self, client: CleanlinessClient | None = None) -> None:
        self.client = client or OpenAICleanlinessClient()

    def inspect_image(
        self,
        source_path: Path,
        inspected_path: Path | None = None,
        prompt_profile: str = DEFAULT_CLEANLINESS_PROMPT_PROFILE,
    ) -> CleanlinessResult:
        if source_path.suffix.lower() not in CLEANLINESS_IMAGE_EXTENSIONS:
            raise ValueError("image file must be png, jpg, jpeg, bmp, or webp")

        target_path = inspected_path or source_path
        if target_path.suffix.lower() not in CLEANLINESS_IMAGE_EXTENSIONS:
            raise ValueError("inspection image file must be png, jpg, jpeg, bmp, or webp")

        normalized_prompt_profile = normalize_cleanliness_prompt_profile(prompt_profile)
        assessment = self.client.analyze_cleanliness(target_path, prompt_profile=normalized_prompt_profile)
        return CleanlinessResult(
            source_path=source_path,
            inspected_path=target_path,
            score=assessment.score,
            confidence=assessment.confidence,
            summary=assessment.summary,
            findings=assessment.findings,
            exact_objects=assessment.exact_objects,
            estimated_objects=assessment.estimated_objects,
            prompt_profile=normalized_prompt_profile,
        )
