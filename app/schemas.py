from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def safe_filename_part(value: str) -> str:
    banned = '<>:"/\\|?*'
    sanitized = "".join("_" if ch in banned else ch for ch in value.strip())
    sanitized = "_".join(filter(None, sanitized.split()))
    return sanitized or "unnamed"


def normalize_points_counterclockwise(points: list["Point"]) -> list["Point"]:
    if len(points) != 4:
        raise ValueError("roi must contain exactly 4 points")

    centroid_x = sum(point.x for point in points) / len(points)
    centroid_y = sum(point.y for point in points) / len(points)

    sorted_points = sorted(
        points,
        key=lambda point: math.atan2(centroid_y - point.y, point.x - centroid_x),
    )

    start_index = min(range(len(sorted_points)), key=lambda idx: (sorted_points[idx].x, sorted_points[idx].y))
    return sorted_points[start_index:] + sorted_points[:start_index]


def normalize_points_to_rectangle(points: list["Point"]) -> list["Point"]:
    if len(points) != 4:
        raise ValueError("roi must contain exactly 4 points")

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    left = min(xs)
    right = max(xs)
    top = min(ys)
    bottom = max(ys)
    if left == right or top == bottom:
        raise ValueError("roi rectangle must have non-zero width and height")

    return [
        Point(x=left, y=top),
        Point(x=right, y=top),
        Point(x=right, y=bottom),
        Point(x=left, y=bottom),
    ]


@dataclass
class Point:
    x: int
    y: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Point":
        return cls(x=int(payload["x"]), y=int(payload["y"]))

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


@dataclass
class ROI:
    name: str
    points: list[Point]

    def __post_init__(self) -> None:
        if len(self.points) != 4:
            raise ValueError(f"roi {self.name} must contain exactly 4 points")
        if len({(point.x, point.y) for point in self.points}) != 4:
            raise ValueError(f"roi {self.name} must contain 4 distinct points")
        self.points = normalize_points_to_rectangle(self.points)

    @classmethod
    def from_rectangle(cls, name: str, x: int, y: int, width: int, height: int) -> "ROI":
        return cls(
            name=name,
            points=[
                Point(x=x, y=y),
                Point(x=x + width, y=y),
                Point(x=x + width, y=y + height),
                Point(x=x, y=y + height),
            ],
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ROI":
        if "points" in payload:
            return cls(
                name=str(payload["name"]),
                points=[Point.from_dict(item) for item in payload["points"]],
            )
        return cls.from_rectangle(
            name=str(payload["name"]),
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
        )

    @property
    def bounds(self) -> dict[str, int]:
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        return {
            "x": min(xs),
            "y": min(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }

    def point_pairs(self) -> list[tuple[int, int]]:
        return [(point.x, point.y) for point in self.points]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": [point.to_dict() for point in self.points],
            "bounds": self.bounds,
        }


@dataclass
class CCTVConfig:
    store_name: str
    cctv_nickname: str
    reference_image_path: str
    areas: list[ROI] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def cctv_id(self) -> str:
        return f"{self.store_name}::{self.cctv_nickname}"

    @property
    def config_id(self) -> str:
        return f"{safe_filename_part(self.store_name)}_{safe_filename_part(self.cctv_nickname)}"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CCTVConfig":
        return cls(
            store_name=str(payload["store_name"]),
            cctv_nickname=str(payload["cctv_nickname"]),
            reference_image_path=str(payload["reference_image_path"]),
            areas=[ROI.from_dict(item) for item in payload.get("areas", [])],
            created_at=str(payload.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_name": self.store_name,
            "cctv_nickname": self.cctv_nickname,
            "cctv_id": self.cctv_id,
            "config_id": self.config_id,
            "reference_image_path": self.reference_image_path,
            "areas": [area.to_dict() for area in self.areas],
            "created_at": self.created_at,
        }
