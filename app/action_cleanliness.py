from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from app.schemas import ROI


ACTION_DECISION_CLEANED = "cleaned_likely"
ACTION_DECISION_NEEDS_CHECK = "needs_check"
ACTION_DECISION_UNKNOWN = "unknown"
ACTION_CLEANED_DWELL_SECONDS = 35.0
ACTION_NEEDS_CHECK_DWELL_SECONDS = 15.0

SAMPLE_TRAJECTORY_JSON = """[
  {"t": 0, "x": 120, "y": 260, "staff_id": "staff-a"},
  {"t": 8, "x": 165, "y": 250, "staff_id": "staff-a"},
  {"t": 18, "x": 235, "y": 245, "staff_id": "staff-a"},
  {"t": 30, "x": 310, "y": 255, "staff_id": "staff-a"},
  {"t": 42, "x": 315, "y": 330, "staff_id": "staff-a"},
  {"t": 55, "x": 225, "y": 340, "staff_id": "staff-a"},
  {"t": 68, "x": 150, "y": 330, "staff_id": "staff-a"},
  {"t": 82, "x": 110, "y": 285, "staff_id": "staff-a"}
]"""


@dataclass(frozen=True)
class TrajectoryPoint:
    t: float
    x: float
    y: float
    staff_id: str = "staff"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrajectoryPoint":
        if "t" not in payload or "x" not in payload or "y" not in payload:
            raise ValueError("trajectory point must include t, x, and y")
        return cls(
            t=float(payload["t"]),
            x=float(payload["x"]),
            y=float(payload["y"]),
            staff_id=str(payload.get("staff_id") or payload.get("marker_id") or "staff"),
        )


@dataclass(frozen=True)
class ActionCleanlinessResult:
    decision: str
    confidence: float
    summary: str
    evidence: list[str]
    features: dict[str, float | int | list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "evidence": self.evidence,
            "features": self.features,
        }


def parse_trajectory_json(raw_json: str) -> list[TrajectoryPoint]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid trajectory JSON: {exc}") from exc

    if isinstance(payload, dict):
        payload = payload.get("points")
    if not isinstance(payload, list):
        raise ValueError("trajectory JSON must be a list or an object with a points list")

    points: list[TrajectoryPoint] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("trajectory points must be objects")
        points.append(TrajectoryPoint.from_dict(item))
    return points


def distance_to_rect(point: TrajectoryPoint, bounds: dict[str, int]) -> float:
    x_min = float(bounds["x"])
    y_min = float(bounds["y"])
    x_max = x_min + float(bounds["width"])
    y_max = y_min + float(bounds["height"])
    dx = max(x_min - point.x, 0.0, point.x - x_max)
    dy = max(y_min - point.y, 0.0, point.y - y_max)
    return math.hypot(dx, dy)


def point_sides_near_rect(point: TrajectoryPoint, bounds: dict[str, int], margin: float) -> set[str]:
    x_min = float(bounds["x"])
    y_min = float(bounds["y"])
    x_max = x_min + float(bounds["width"])
    y_max = y_min + float(bounds["height"])
    inside_expanded_y = y_min - margin <= point.y <= y_max + margin
    inside_expanded_x = x_min - margin <= point.x <= x_max + margin

    sides: set[str] = set()
    if inside_expanded_y and abs(point.x - x_min) <= margin:
        sides.add("left")
    if inside_expanded_y and abs(point.x - x_max) <= margin:
        sides.add("right")
    if inside_expanded_x and abs(point.y - y_min) <= margin:
        sides.add("top")
    if inside_expanded_x and abs(point.y - y_max) <= margin:
        sides.add("bottom")
    return sides


class ActionCleanlinessService:
    def __init__(
        self,
        near_margin: float = 80.0,
        max_gap_seconds: float = 30.0,
        stop_speed_pixels_per_second: float = 18.0,
    ) -> None:
        self.near_margin = near_margin
        self.max_gap_seconds = max_gap_seconds
        self.stop_speed_pixels_per_second = stop_speed_pixels_per_second

    def evaluate(
        self,
        roi: ROI,
        trajectory_points: list[TrajectoryPoint],
        *,
        near_margin: float | None = None,
        manual_confirmed: bool = False,
    ) -> ActionCleanlinessResult:
        if not trajectory_points:
            return ActionCleanlinessResult(
                decision=ACTION_DECISION_UNKNOWN,
                confidence=0.0,
                summary="No staff marker trajectory was provided after checkout.",
                evidence=["결제 이후 직원 배지 좌표가 없습니다."],
                features=self.empty_features(),
            )

        margin = float(near_margin if near_margin is not None else self.near_margin)
        bounds = roi.bounds
        points_by_staff = self.group_points(trajectory_points)

        near_point_count = 0
        visited_sides: set[str] = set()
        dwell_seconds = 0.0
        stopped_seconds = 0.0
        approach_count = 0
        side_transition_count = 0
        staff_with_near_visit: set[str] = set()

        all_times = [point.t for point in trajectory_points]
        observation_seconds = max(all_times) - min(all_times) if all_times else 0.0

        for staff_id, points in points_by_staff.items():
            sorted_points = sorted(points, key=lambda item: item.t)
            previous_near = False
            previous_side: str | None = None

            for index, point in enumerate(sorted_points):
                is_near = distance_to_rect(point, bounds) <= margin
                sides = point_sides_near_rect(point, bounds, margin)
                if is_near:
                    near_point_count += 1
                    staff_with_near_visit.add(staff_id)
                    visited_sides.update(sides)
                    current_side = sorted(sides)[0] if sides else None
                    if previous_side and current_side and previous_side != current_side:
                        side_transition_count += 1
                    if current_side:
                        previous_side = current_side
                if is_near and not previous_near:
                    approach_count += 1
                previous_near = is_near

                if index + 1 >= len(sorted_points):
                    continue
                next_point = sorted_points[index + 1]
                interval = next_point.t - point.t
                if interval <= 0 or interval > self.max_gap_seconds:
                    continue
                next_near = distance_to_rect(next_point, bounds) <= margin
                if not (is_near and next_near):
                    continue
                dwell_seconds += interval
                distance = math.hypot(next_point.x - point.x, next_point.y - point.y)
                speed = distance / interval
                if speed <= self.stop_speed_pixels_per_second:
                    stopped_seconds += interval

        coverage_ratio = len(visited_sides) / 4.0
        features: dict[str, float | int | list[str]] = {
            "observation_seconds": round(observation_seconds, 2),
            "near_point_count": near_point_count,
            "dwell_seconds": round(dwell_seconds, 2),
            "stopped_seconds": round(stopped_seconds, 2),
            "coverage_ratio": round(coverage_ratio, 3),
            "visited_sides": sorted(visited_sides),
            "approach_count": approach_count,
            "side_transition_count": side_transition_count,
            "staff_count": len(points_by_staff),
            "staff_with_near_visit_count": len(staff_with_near_visit),
            "near_margin": round(margin, 2),
        }

        return self.classify(features, manual_confirmed=manual_confirmed)

    @staticmethod
    def group_points(points: list[TrajectoryPoint]) -> dict[str, list[TrajectoryPoint]]:
        grouped: dict[str, list[TrajectoryPoint]] = {}
        for point in points:
            grouped.setdefault(point.staff_id, []).append(point)
        return grouped

    @staticmethod
    def empty_features() -> dict[str, float | int | list[str]]:
        return {
            "observation_seconds": 0.0,
            "near_point_count": 0,
            "dwell_seconds": 0.0,
            "stopped_seconds": 0.0,
            "coverage_ratio": 0.0,
            "visited_sides": [],
            "approach_count": 0,
            "side_transition_count": 0,
            "staff_count": 0,
            "staff_with_near_visit_count": 0,
            "near_margin": 0.0,
        }

    @staticmethod
    def classify(
        features: dict[str, float | int | list[str]],
        *,
        manual_confirmed: bool,
    ) -> ActionCleanlinessResult:
        dwell_seconds = float(features["dwell_seconds"])
        evidence = [
            f"테이블 주변 체류 시간 {dwell_seconds:.1f}초",
            f"청소 완료 기준 {ACTION_CLEANED_DWELL_SECONDS:.0f}초 이상",
            f"확인 필요 기준 {ACTION_NEEDS_CHECK_DWELL_SECONDS:.0f}초 미만",
        ]

        if manual_confirmed:
            return ActionCleanlinessResult(
                decision=ACTION_DECISION_CLEANED,
                confidence=0.95,
                summary="직원 확인 입력이 있어 청소 완료 가능성이 높습니다.",
                evidence=evidence + ["직원 수동 확인이 포함되었습니다."],
                features=features,
            )

        if dwell_seconds >= ACTION_CLEANED_DWELL_SECONDS:
            confidence = min(0.95, 0.7 + min((dwell_seconds - ACTION_CLEANED_DWELL_SECONDS) / 60.0, 0.25))
            return ActionCleanlinessResult(
                decision=ACTION_DECISION_CLEANED,
                confidence=confidence,
                summary="결제 이후 직원이 테이블 주변에 충분한 시간 동안 머물렀습니다.",
                evidence=evidence,
                features=features,
            )

        if dwell_seconds < ACTION_NEEDS_CHECK_DWELL_SECONDS:
            confidence = min(0.9, 0.65 + min((ACTION_NEEDS_CHECK_DWELL_SECONDS - dwell_seconds) / 60.0, 0.25))
            return ActionCleanlinessResult(
                decision=ACTION_DECISION_NEEDS_CHECK,
                confidence=confidence,
                summary="결제 이후 직원이 테이블 주변에 머문 시간이 부족합니다.",
                evidence=evidence,
                features=features,
            )

        confidence = min(0.62, max(0.3, dwell_seconds / ACTION_CLEANED_DWELL_SECONDS * 0.6))
        return ActionCleanlinessResult(
            decision=ACTION_DECISION_UNKNOWN,
            confidence=confidence,
            summary="테이블 주변 체류 시간만으로는 청소 완료 여부를 확정하기 어렵습니다.",
            evidence=evidence,
            features=features,
        )
