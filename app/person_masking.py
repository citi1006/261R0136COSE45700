from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from app.analysis import read_image, write_image
from app.config import PERSON_MASK_DIR


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MASK2FORMER_MODEL_ID = "facebook/mask2former-swin-small-coco-instance"
PERSON_LABEL = "person"


@dataclass(frozen=True)
class PersonDetection:
    x: int
    y: int
    width: int
    height: int
    score: float
    source: str

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "score": round(self.score, 3),
            "source": self.source,
        }


@dataclass
class PersonMaskResult:
    source_path: Path
    masked_path: Path
    person_count: int
    masked_pixel_ratio: float
    detections: list[PersonDetection]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "masked_path": str(self.masked_path),
            "person_count": self.person_count,
            "masked_pixel_ratio": round(self.masked_pixel_ratio, 4),
            "detections": [detection.to_dict() for detection in self.detections],
        }


class PersonSegmenter(Protocol):
    def segment_people(self, image: np.ndarray) -> tuple[np.ndarray, list[PersonDetection]]:
        ...


class Mask2FormerPersonSegmenter:
    def __init__(
        self,
        model_id: str = MASK2FORMER_MODEL_ID,
        threshold: float = 0.5,
        mask_threshold: float = 0.5,
        device: str | None = None,
    ) -> None:
        self.model_id = model_id
        self.threshold = threshold
        self.mask_threshold = mask_threshold
        self.device = device
        self._processor = None
        self._model = None
        self._torch = None
        self._person_label_id: int | None = None

    def _load(self) -> tuple[object, object, object, str]:
        if self._processor is None or self._model is None or self._torch is None:
            try:
                import torch
                from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation
            except ImportError as exc:
                raise RuntimeError(
                    "Mask2Former person masking requires torch, torchvision, and transformers. "
                    "Install project dependencies with `pip install -r requirements.txt`."
                ) from exc

            if self.device is None:
                if torch.cuda.is_available():
                    self.device = "cuda"
                elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                    self.device = "mps"
                else:
                    self.device = "cpu"

            self._processor = AutoImageProcessor.from_pretrained(self.model_id)
            self._model = Mask2FormerForUniversalSegmentation.from_pretrained(self.model_id)
            self._model.to(self.device)
            self._model.eval()
            self._torch = torch
            self._person_label_id = self._resolve_person_label_id()

        return self._processor, self._model, self._torch, self.device or "cpu"

    def _resolve_person_label_id(self) -> int:
        if self._model is None:
            raise RuntimeError("Mask2Former model is not loaded.")

        label2id = getattr(self._model.config, "label2id", {}) or {}
        person_id = label2id.get(PERSON_LABEL)
        if person_id is not None:
            return int(person_id)

        id2label = getattr(self._model.config, "id2label", {}) or {}
        for raw_label_id, label in id2label.items():
            if str(label).lower() == PERSON_LABEL:
                return int(raw_label_id)

        raise RuntimeError(f"{self.model_id} does not expose a COCO `person` label.")

    @staticmethod
    def _to_pil_rgb(image: np.ndarray) -> Image.Image:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("person masking expects a BGR color image")
        rgb_image = image[:, :, ::-1]
        return Image.fromarray(np.ascontiguousarray(rgb_image))

    @staticmethod
    def _move_inputs_to_device(inputs: object, device: str) -> object:
        if hasattr(inputs, "to"):
            return inputs.to(device)
        if isinstance(inputs, dict):
            return {
                key: value.to(device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
        return inputs

    @staticmethod
    def _as_numpy_mask(segmentation: object) -> np.ndarray:
        if hasattr(segmentation, "detach"):
            segmentation = segmentation.detach().cpu().numpy()
        return np.asarray(segmentation)

    @staticmethod
    def _detection_from_mask(instance_mask: np.ndarray, score: float) -> PersonDetection | None:
        ys, xs = np.where(instance_mask)
        if ys.size == 0 or xs.size == 0:
            return None

        x_min = int(xs.min())
        y_min = int(ys.min())
        x_max = int(xs.max())
        y_max = int(ys.max())
        return PersonDetection(
            x=x_min,
            y=y_min,
            width=x_max - x_min + 1,
            height=y_max - y_min + 1,
            score=score,
            source="mask2former",
        )

    def segment_people(self, image: np.ndarray) -> tuple[np.ndarray, list[PersonDetection]]:
        processor, model, torch, device = self._load()
        pil_image = self._to_pil_rgb(image)

        inputs = processor(images=pil_image, return_tensors="pt")
        inputs = self._move_inputs_to_device(inputs, device)

        with torch.no_grad():
            outputs = model(**inputs)

        result = processor.post_process_instance_segmentation(
            outputs,
            threshold=self.threshold,
            mask_threshold=self.mask_threshold,
            target_sizes=[(pil_image.height, pil_image.width)],
        )[0]

        segmentation = result.get("segmentation")
        image_height, image_width = image.shape[:2]
        combined_mask = np.zeros((image_height, image_width), dtype=np.uint8)
        detections: list[PersonDetection] = []

        if segmentation is None:
            return combined_mask, detections

        segmentation_map = self._as_numpy_mask(segmentation)
        for segment in result.get("segments_info", []):
            if int(segment.get("label_id", -1)) != self._person_label_id:
                continue

            instance_mask = segmentation_map == int(segment["id"])
            combined_mask[instance_mask] = 255

            detection = self._detection_from_mask(
                instance_mask,
                score=float(segment.get("score", 0.0)),
            )
            if detection is not None:
                detections.append(detection)

        return combined_mask, detections


class PersonMaskService:
    def __init__(self, segmenter: PersonSegmenter | None = None) -> None:
        self.segmenter = segmenter or Mask2FormerPersonSegmenter()

    def apply_black_mask(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[PersonDetection]]:
        mask, detections = self.segmenter.segment_people(image)
        if mask.shape != image.shape[:2]:
            raise ValueError("person mask shape does not match source image")

        masked_image = image.copy()
        masked_image[mask > 0] = (0, 0, 0)
        return masked_image, mask.astype(np.uint8), detections

    def mask_image_file(self, source_path: Path, output_stem: str | None = None) -> PersonMaskResult:
        if source_path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("image file must be png, jpg, jpeg, bmp, or webp")

        image = read_image(source_path)
        masked_image, mask, detections = self.apply_black_mask(image)

        safe_stem = output_stem or source_path.stem
        output_path = PERSON_MASK_DIR / f"{safe_stem}_person_masked.png"
        write_image(output_path, masked_image)

        total_pixels = max(1, mask.shape[0] * mask.shape[1])
        masked_pixel_ratio = float(np.count_nonzero(mask) / total_pixels)
        return PersonMaskResult(
            source_path=source_path,
            masked_path=output_path,
            person_count=len(detections),
            masked_pixel_ratio=masked_pixel_ratio,
            detections=detections,
        )
