from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.action_cleanliness import (
    ACTION_DECISION_CLEANED,
    ACTION_DECISION_NEEDS_CHECK,
    ACTION_DECISION_UNKNOWN,
    ActionCleanlinessResult,
)
from app.cleanliness import CleanlinessResult


OBJECT_CONFIDENCE_THRESHOLD = 0.6
HYBRID_STAGE_OBJECT = "object_based"
HYBRID_STAGE_ACTION = "action_fallback"


@dataclass(frozen=True)
class HybridCleanlinessResult:
    final_decision: str
    final_confidence: float
    final_stage: str
    summary: str
    fallback_triggered: bool
    object_confidence_threshold: float
    object_result: CleanlinessResult
    action_result: ActionCleanlinessResult | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_decision": self.final_decision,
            "final_confidence": round(self.final_confidence, 3),
            "final_stage": self.final_stage,
            "summary": self.summary,
            "fallback_triggered": self.fallback_triggered,
            "object_confidence_threshold": self.object_confidence_threshold,
            "object_result": self.object_result.to_dict(),
            "action_result": self.action_result.to_dict() if self.action_result is not None else None,
        }


def object_score_to_decision(score: int) -> str:
    if score >= 4:
        return ACTION_DECISION_CLEANED
    if score <= 2:
        return ACTION_DECISION_NEEDS_CHECK
    return ACTION_DECISION_UNKNOWN


def build_hybrid_cleanliness_result(
    object_result: CleanlinessResult,
    action_result: ActionCleanlinessResult | None,
    *,
    object_confidence_threshold: float = OBJECT_CONFIDENCE_THRESHOLD,
) -> HybridCleanlinessResult:
    fallback_triggered = object_result.confidence < object_confidence_threshold

    if fallback_triggered:
        if action_result is None:
            action_result = ActionCleanlinessResult(
                decision=ACTION_DECISION_UNKNOWN,
                score=1,
                confidence=0.0,
                summary="물체 기반 청결도 신뢰도가 낮지만 Action 동선 평가 입력이 없어 최종 판단을 보류했습니다.",
                evidence=["물체 기반 confidence가 임계치보다 낮습니다.", "Action trajectory가 제공되지 않았습니다."],
                features={},
            )
        return HybridCleanlinessResult(
            final_decision=action_result.decision,
            final_confidence=action_result.confidence,
            final_stage=HYBRID_STAGE_ACTION,
            summary=(
                f"물체 기반 confidence {object_result.confidence:.2f}가 "
                f"임계치 {object_confidence_threshold:.2f}보다 낮아 Action 평가로 전환했습니다. "
                f"{action_result.summary}"
            ),
            fallback_triggered=True,
            object_confidence_threshold=object_confidence_threshold,
            object_result=object_result,
            action_result=action_result,
        )

    decision = object_score_to_decision(object_result.score)
    return HybridCleanlinessResult(
        final_decision=decision,
        final_confidence=object_result.confidence,
        final_stage=HYBRID_STAGE_OBJECT,
        summary=(
            f"물체 기반 confidence {object_result.confidence:.2f}가 "
            f"임계치 {object_confidence_threshold:.2f} 이상이므로 물체 기반 청결도 평가를 최종 결과로 사용했습니다. "
            f"{object_result.summary}"
        ),
        fallback_triggered=False,
        object_confidence_threshold=object_confidence_threshold,
        object_result=object_result,
        action_result=None,
    )
