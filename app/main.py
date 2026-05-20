from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.action_cleanliness import ActionCleanlinessService, SAMPLE_TRAJECTORY_JSON, parse_trajectory_json
from app.analysis import AnalysisService, read_image, save_analysis_crop
from app.cleanliness import (
    CLEANLINESS_IMAGE_EXTENSIONS,
    DEFAULT_CLEANLINESS_PROMPT_PROFILE,
    CleanlinessService,
    normalize_cleanliness_prompt_profile,
)
from app.config import DATA_DIR, POSTER_TEMPLATE_DIR, UPLOAD_DIR
from app.database import (
    fetch_cleanliness_filter_options,
    fetch_cleanliness_results,
    fetch_cleanliness_store_summary,
    fetch_filter_options,
    fetch_latest_by_roi,
    fetch_results,
    init_db,
    insert_cleanliness_result,
)
from app.hybrid_cleanliness import OBJECT_CONFIDENCE_THRESHOLD, build_hybrid_cleanliness_result, object_score_to_decision
from app.person_masking import IMAGE_EXTENSIONS, PersonMaskService
from app.roi_store import ConfigStore
from app.schemas import ROI


app = FastAPI(title="MVP1 Franchise Quality Monitor")
templates = Jinja2Templates(directory="app/templates")
config_store = ConfigStore()
analysis_service = AnalysisService()
person_mask_service = PersonMaskService()
cleanliness_service = CleanlinessService()
action_cleanliness_service = ActionCleanlinessService()

init_db()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")


def save_upload(upload: UploadFile, target_dir: Path, fallback_stem: str) -> Path:
    suffix = Path(upload.filename or "").suffix.lower() or ".bin"
    target_path = target_dir / f"{fallback_stem}{suffix}"
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return target_path


def config_payload(config_id: str | None) -> dict[str, Any] | None:
    if not config_id:
        return None
    try:
        return config_store.load(config_id).to_dict()
    except FileNotFoundError:
        return None


def with_human_clear_ratio(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    if "visible_ratio" in payload and "human_clear_ratio" not in payload:
        payload["human_clear_ratio"] = payload["visible_ratio"]
    return payload


@app.get("/")
def dashboard(request: Request) -> Any:
    configs = config_store.list_configs()
    stores = config_store.list_store_names()
    latest_pop = [with_human_clear_ratio(row) for row in fetch_latest_by_roi("POP")]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "configs": configs,
            "stores": stores,
            "latest_pop": latest_pop,
            "absent_pop_count": len([row for row in latest_pop if row["decision"] == "Absent"]),
        },
    )


@app.get("/stores")
def stores_page(request: Request) -> Any:
    store_summaries = config_store.list_store_summaries()
    return templates.TemplateResponse(
        request,
        "stores.html",
        {
            "request": request,
            "title": "Stores",
            "stores": store_summaries,
        },
    )


@app.get("/setup")
def setup_page(
    request: Request,
    config_id: str | None = Query(default=None),
    store_name: str | None = Query(default=None),
) -> Any:
    store_summaries = config_store.list_store_summaries()
    selected = config_payload(config_id)
    selected_store_name = selected["store_name"] if selected else (store_name or "")
    selected_store_configs = config_store.list_configs_by_store(selected_store_name) if selected_store_name else []
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "title": "ROI Setup",
            "stores": store_summaries,
            "selected_store_name": selected_store_name,
            "selected_store_configs": selected_store_configs,
            "selected_config": selected,
        },
    )


@app.post("/stores")
async def create_store(store_name: str = Form(...)) -> RedirectResponse:
    try:
        normalized = store_name.strip()
        config_store.add_store_name(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/stores?store_name={quote_plus(normalized)}", status_code=303)


@app.post("/setup/save")
async def save_setup(
    store_name: str = Form(...),
    cctv_nickname: str = Form(...),
    rois_json: str = Form(...),
    existing_reference_path: str = Form(default=""),
    reference_image: UploadFile | None = File(default=None),
) -> RedirectResponse:
    try:
        raw_rois = json.loads(rois_json)
        rois = [ROI.from_dict(item) for item in raw_rois]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid roi payload: {exc}") from exc

    temp_path: Path | None = None
    if reference_image and reference_image.filename:
        suffix = Path(reference_image.filename).suffix.lower() or ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            shutil.copyfileobj(reference_image.file, temp_file)
            temp_path = Path(temp_file.name)

    try:
        config = config_store.save_config(
            store_name=store_name,
            cctv_nickname=cctv_nickname,
            rois=rois,
            reference_image_source=temp_path,
            existing_reference_path=existing_reference_path or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return RedirectResponse(
        url=f"/setup?store_name={quote_plus(config.store_name)}&config_id={config.config_id}",
        status_code=303,
    )


@app.get("/api/configs")
def list_configs() -> list[dict[str, Any]]:
    return [config.to_dict() for config in config_store.list_configs()]


@app.get("/api/stores")
def list_stores() -> list[dict[str, int | str]]:
    return config_store.list_store_summaries()


@app.get("/api/configs/{config_id}")
def get_config(config_id: str) -> dict[str, Any]:
    try:
        return config_store.load(config_id).to_dict()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc


@app.get("/api/configs/{config_id}/rois/{roi_name}")
def get_roi(config_id: str, roi_name: str) -> dict[str, Any]:
    try:
        return config_store.get_roi(config_id, roi_name).to_dict()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc


@app.get("/analyze")
def analyze_page(request: Request, config_id: str | None = Query(default=None)) -> Any:
    configs = config_store.list_configs()
    poster_templates = sorted(POSTER_TEMPLATE_DIR.glob("*"))
    selected = config_payload(config_id)
    return templates.TemplateResponse(
        request,
        "analyze.html",
        {
            "request": request,
            "title": "Analysis",
            "configs": configs,
            "configs_payload": [config.to_dict() for config in configs],
            "poster_templates": [path.name for path in poster_templates],
            "selected_config": selected,
        },
    )


def data_url(path: Path) -> str:
    return "/data/" + path.relative_to(DATA_DIR).as_posix()


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def merge_report_filter_options(
    pop_options: dict[str, list[str]],
    cleanliness_options: dict[str, list[str]],
) -> dict[str, list[str]]:
    options = dict(pop_options)
    for key in ("store_name", "cctv_id", "roi_name"):
        options[key] = sorted({*pop_options.get(key, []), *cleanliness_options.get(key, [])})
    options["cleanliness_mode"] = cleanliness_options.get("mode", [])
    options["cleanliness_decision"] = cleanliness_options.get("decision", [])
    options["cleanliness_final_stage"] = cleanliness_options.get("final_stage", [])
    return options


def save_cleanliness_record(
    *,
    analyzed_at: datetime,
    config: Any,
    roi: ROI,
    mode: str,
    decision: str,
    score: int | None,
    confidence: float,
    final_stage: str,
    summary: str,
    source_path: Path | None = None,
    crop_path: Path | None = None,
    exact_objects: list[str] | None = None,
    estimated_objects: list[str] | None = None,
    findings: list[str] | None = None,
    action_features: dict[str, Any] | None = None,
) -> None:
    insert_cleanliness_result(
        {
            "analyzed_at": analyzed_at.isoformat(timespec="minutes"),
            "store_name": config.store_name,
            "cctv_id": config.cctv_id,
            "cctv_nickname": config.cctv_nickname,
            "roi_name": roi.name,
            "mode": mode,
            "decision": decision,
            "score": score,
            "confidence": confidence,
            "final_stage": final_stage,
            "summary": summary,
            "source_path": str(source_path) if source_path is not None else "",
            "crop_path": str(crop_path) if crop_path is not None else "",
            "exact_objects": dump_json(exact_objects or []),
            "estimated_objects": dump_json(estimated_objects or []),
            "findings": dump_json(findings or []),
            "action_features": dump_json(action_features or {}),
        }
    )


@app.get("/cleanliness")
def cleanliness_page(request: Request, config_id: str | None = Query(default=None)) -> Any:
    configs = config_store.list_configs()
    selected = config_payload(config_id)
    return templates.TemplateResponse(
        request,
        "cleanliness.html",
        {
            "request": request,
            "title": "청결도 검사",
            "configs": configs,
            "configs_payload": [config.to_dict() for config in configs],
            "selected_config": selected,
            "selected_roi_name": "",
            "selected_prompt_profile": DEFAULT_CLEANLINESS_PROMPT_PROFILE,
        },
    )


@app.post("/cleanliness")
async def cleanliness_upload(
    request: Request,
    config_id: str = Form(...),
    roi_name: str = Form(...),
    prompt_profile: str = Form(default=DEFAULT_CLEANLINESS_PROMPT_PROFILE),
    image_file: UploadFile = File(...),
) -> Any:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    suffix = Path(image_file.filename or "").suffix.lower()
    if suffix not in CLEANLINESS_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="image file must be png, jpg, jpeg, bmp, or webp")

    analyzed_at = datetime.now()
    stem = f"{config_id}_{roi_name}_cleanliness_{analyzed_at.strftime('%Y%m%d%H%M%S')}"
    source_path = save_upload(image_file, UPLOAD_DIR, stem)
    crop_path = save_analysis_crop(read_image(source_path), roi, stem)
    selected_prompt_profile = normalize_cleanliness_prompt_profile(prompt_profile)

    try:
        result = cleanliness_service.inspect_image(
            source_path,
            inspected_path=crop_path,
            prompt_profile=selected_prompt_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "OPENAI_API_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    payload = result.to_dict()
    payload["store_name"] = config.store_name
    payload["cctv_nickname"] = config.cctv_nickname
    payload["roi_name"] = roi.name
    payload["source_url"] = data_url(result.source_path)
    payload["crop_url"] = data_url(crop_path)
    payload["llm_input_url"] = data_url(result.inspected_path)
    save_cleanliness_record(
        analyzed_at=analyzed_at,
        config=config,
        roi=roi,
        mode="object",
        decision=object_score_to_decision(result.score),
        score=result.score,
        confidence=result.confidence,
        final_stage="object_based",
        summary=result.summary,
        source_path=result.source_path,
        crop_path=crop_path,
        exact_objects=result.exact_objects,
        estimated_objects=result.estimated_objects,
        findings=result.findings,
    )

    configs = config_store.list_configs()

    return templates.TemplateResponse(
        request,
        "cleanliness.html",
        {
            "request": request,
            "title": "청결도 검사",
            "configs": configs,
            "configs_payload": [item.to_dict() for item in configs],
            "selected_config": config.to_dict(),
            "selected_roi_name": roi.name,
            "selected_prompt_profile": selected_prompt_profile,
            "cleanliness_result": payload,
        },
    )


@app.get("/person-mask")
def person_mask_page(request: Request) -> Any:
    return templates.TemplateResponse(
        request,
        "person_mask.html",
        {
            "request": request,
            "title": "Person Masking",
        },
    )


@app.post("/person-mask")
async def person_mask_upload(
    request: Request,
    image_file: UploadFile = File(...),
) -> Any:
    suffix = Path(image_file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="image file must be png, jpg, jpeg, bmp, or webp")

    stem = f"person_mask_source_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    source_path = save_upload(image_file, UPLOAD_DIR, stem)

    try:
        result = person_mask_service.mask_image_file(source_path, output_stem=source_path.stem)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.to_dict()
    payload["source_url"] = data_url(result.source_path)
    payload["masked_url"] = data_url(result.masked_path)

    return templates.TemplateResponse(
        request,
        "person_mask.html",
        {
            "request": request,
            "title": "Person Masking",
            "mask_result": payload,
        },
    )


@app.get("/action-cleanliness")
def action_cleanliness_page(request: Request, config_id: str | None = Query(default=None)) -> Any:
    configs = config_store.list_configs()
    selected = config_payload(config_id)
    return templates.TemplateResponse(
        request,
        "action_cleanliness.html",
        {
            "request": request,
            "title": "Action 청결도",
            "configs": configs,
            "configs_payload": [config.to_dict() for config in configs],
            "selected_config": selected,
            "selected_roi_name": "",
            "sample_trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "near_margin": int(action_cleanliness_service.near_margin),
            "manual_confirmed": False,
        },
    )


@app.post("/action-cleanliness")
async def action_cleanliness_submit(
    request: Request,
    config_id: str = Form(...),
    roi_name: str = Form(...),
    trajectory_json: str = Form(...),
    near_margin: float = Form(default=80.0),
    manual_confirmed: bool = Form(default=False),
) -> Any:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    try:
        trajectory_points = parse_trajectory_json(trajectory_json)
        action_result = action_cleanliness_service.evaluate(
            roi,
            trajectory_points,
            near_margin=near_margin,
            manual_confirmed=manual_confirmed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = action_result.to_dict()
    payload["store_name"] = config.store_name
    payload["cctv_nickname"] = config.cctv_nickname
    payload["roi_name"] = roi.name
    save_cleanliness_record(
        analyzed_at=datetime.now(),
        config=config,
        roi=roi,
        mode="action",
        decision=action_result.decision,
        score=None,
        confidence=action_result.confidence,
        final_stage="action_only",
        summary=action_result.summary,
        action_features=action_result.features,
    )

    configs = config_store.list_configs()
    return templates.TemplateResponse(
        request,
        "action_cleanliness.html",
        {
            "request": request,
            "title": "Action 청결도",
            "configs": configs,
            "configs_payload": [item.to_dict() for item in configs],
            "selected_config": config.to_dict(),
            "selected_roi_name": roi.name,
            "sample_trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "trajectory_json": trajectory_json,
            "near_margin": near_margin,
            "manual_confirmed": manual_confirmed,
            "action_result": payload,
        },
    )


@app.get("/hybrid-cleanliness")
def hybrid_cleanliness_page(request: Request, config_id: str | None = Query(default=None)) -> Any:
    configs = config_store.list_configs()
    selected = config_payload(config_id)
    return templates.TemplateResponse(
        request,
        "hybrid_cleanliness.html",
        {
            "request": request,
            "title": "Hybrid 청결도",
            "configs": configs,
            "configs_payload": [config.to_dict() for config in configs],
            "selected_config": selected,
            "selected_roi_name": "",
            "selected_prompt_profile": DEFAULT_CLEANLINESS_PROMPT_PROFILE,
            "sample_trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "near_margin": int(action_cleanliness_service.near_margin),
            "manual_confirmed": False,
            "object_confidence_threshold": OBJECT_CONFIDENCE_THRESHOLD,
        },
    )


@app.post("/hybrid-cleanliness")
async def hybrid_cleanliness_submit(
    request: Request,
    config_id: str = Form(...),
    roi_name: str = Form(...),
    prompt_profile: str = Form(default=DEFAULT_CLEANLINESS_PROMPT_PROFILE),
    trajectory_json: str = Form(...),
    near_margin: float = Form(default=80.0),
    manual_confirmed: bool = Form(default=False),
    image_file: UploadFile = File(...),
) -> Any:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    suffix = Path(image_file.filename or "").suffix.lower()
    if suffix not in CLEANLINESS_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="image file must be png, jpg, jpeg, bmp, or webp")

    analyzed_at = datetime.now()
    stem = f"{config_id}_{roi_name}_hybrid_cleanliness_{analyzed_at.strftime('%Y%m%d%H%M%S')}"
    source_path = save_upload(image_file, UPLOAD_DIR, stem)
    crop_path = save_analysis_crop(read_image(source_path), roi, stem)
    selected_prompt_profile = normalize_cleanliness_prompt_profile(prompt_profile)

    try:
        object_result = cleanliness_service.inspect_image(
            source_path,
            inspected_path=crop_path,
            prompt_profile=selected_prompt_profile,
        )

        action_result = None
        if object_result.confidence < OBJECT_CONFIDENCE_THRESHOLD:
            trajectory_points = parse_trajectory_json(trajectory_json)
            action_result = action_cleanliness_service.evaluate(
                roi,
                trajectory_points,
                near_margin=near_margin,
                manual_confirmed=manual_confirmed,
            )

        hybrid_result = build_hybrid_cleanliness_result(
            object_result,
            action_result,
            object_confidence_threshold=OBJECT_CONFIDENCE_THRESHOLD,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "OPENAI_API_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    payload = hybrid_result.to_dict()
    payload["store_name"] = config.store_name
    payload["cctv_nickname"] = config.cctv_nickname
    payload["roi_name"] = roi.name
    payload["object_result"]["source_url"] = data_url(object_result.source_path)
    payload["object_result"]["crop_url"] = data_url(crop_path)
    payload["object_result"]["llm_input_url"] = data_url(object_result.inspected_path)
    save_cleanliness_record(
        analyzed_at=analyzed_at,
        config=config,
        roi=roi,
        mode="hybrid",
        decision=hybrid_result.final_decision,
        score=object_result.score,
        confidence=hybrid_result.final_confidence,
        final_stage=hybrid_result.final_stage,
        summary=hybrid_result.summary,
        source_path=object_result.source_path,
        crop_path=crop_path,
        exact_objects=object_result.exact_objects,
        estimated_objects=object_result.estimated_objects,
        findings=object_result.findings,
        action_features=hybrid_result.action_result.features if hybrid_result.action_result is not None else {},
    )

    configs = config_store.list_configs()
    return templates.TemplateResponse(
        request,
        "hybrid_cleanliness.html",
        {
            "request": request,
            "title": "Hybrid 청결도",
            "configs": configs,
            "configs_payload": [item.to_dict() for item in configs],
            "selected_config": config.to_dict(),
            "selected_roi_name": roi.name,
            "selected_prompt_profile": selected_prompt_profile,
            "sample_trajectory_json": SAMPLE_TRAJECTORY_JSON,
            "trajectory_json": trajectory_json,
            "near_margin": near_margin,
            "manual_confirmed": manual_confirmed,
            "object_confidence_threshold": OBJECT_CONFIDENCE_THRESHOLD,
            "hybrid_result": payload,
        },
    )


@app.post("/analyze")
async def analyze_upload(
    request: Request,
    config_id: str = Form(...),
    roi_name: str = Form(...),
    sensor_brightness: float | None = Form(default=None),
    enable_sensor_match: bool = Form(default=False),
    media_file: UploadFile = File(...),
    poster_template_upload: UploadFile | None = File(default=None),
    poster_template_name: str = Form(default=""),
) -> Any:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    media_path = save_upload(media_file, UPLOAD_DIR, f"{config_id}_{roi_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}")

    if poster_template_upload and poster_template_upload.filename:
        poster_path = save_upload(
            poster_template_upload,
            POSTER_TEMPLATE_DIR,
            f"{config_id}_{roi_name}_poster_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )
    elif poster_template_name:
        poster_path = POSTER_TEMPLATE_DIR / poster_template_name
        if not poster_path.exists():
            raise HTTPException(status_code=400, detail="poster template not found")
    else:
        raise HTTPException(status_code=400, detail="poster template is required")

    try:
        result = analysis_service.analyze_media(
            config=config,
            roi=roi,
            media_path=media_path,
            poster_template_path=poster_path,
            sensor_brightness=sensor_brightness,
            enable_sensor_match=enable_sensor_match,
        )
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "OPENAI_API_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    configs = config_store.list_configs()
    return templates.TemplateResponse(
        request,
        "analyze.html",
        {
            "request": request,
            "title": "Analysis",
            "configs": configs,
            "configs_payload": [item.to_dict() for item in configs],
            "poster_templates": [path.name for path in sorted(POSTER_TEMPLATE_DIR.glob("*"))],
            "selected_config": config.to_dict(),
            "analysis_result": result,
        },
    )


@app.post("/api/validate-image")
async def validate_image_api(
    config_id: str = Form(...),
    roi_name: str = Form(...),
    image_file: UploadFile = File(...),
) -> JSONResponse:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    image_path = save_upload(image_file, UPLOAD_DIR, f"{config_id}_{roi_name}_validate_image")
    try:
        validation = analysis_service.validator.validate_image(read_image(image_path), roi, source_path=image_path)
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "OPENAI_API_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return JSONResponse(
        {
            "is_valid": validation.is_valid,
            "human_clear_ratio": round(validation.visible_ratio, 3),
            "occlusion_level": validation.occlusion_level,
            "summary": validation.summary,
            "reject_reason": validation.reject_reason,
        }
    )


@app.post("/api/validate-video")
async def validate_video_api(
    config_id: str = Form(...),
    roi_name: str = Form(...),
    sensor_brightness: float | None = Form(default=None),
    enable_sensor_match: bool = Form(default=False),
    video_file: UploadFile = File(...),
) -> JSONResponse:
    try:
        config = config_store.load(config_id)
        roi = config_store.get_roi(config_id, roi_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="config not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="roi not found") from exc

    video_path = save_upload(video_file, UPLOAD_DIR, f"{config_id}_{roi_name}_validate_video")
    try:
        validation = analysis_service.validator.validate_video(
            video_path,
            roi,
            sensor_brightness=sensor_brightness,
            enable_sensor_match=enable_sensor_match,
        )
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "OPENAI_API_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return JSONResponse(
        {
            "is_valid": validation.is_valid,
            "human_clear_ratio": round(validation.visible_ratio, 3),
            "occlusion_duration": round(validation.occlusion_duration, 2),
            "brightness_mismatch_duration": round(validation.brightness_mismatch_duration, 2),
            "occlusion_level": validation.occlusion_level,
            "summary": validation.summary,
            "reject_reason": validation.reject_reason,
        }
    )


@app.get("/reports")
def reports_page(
    request: Request,
    store_name: str | None = Query(default=None),
    cctv_id: str | None = Query(default=None),
    roi_name: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    cleanliness_mode: str | None = Query(default=None),
    cleanliness_decision: str | None = Query(default=None),
    cleanliness_final_stage: str | None = Query(default=None),
) -> Any:
    filters = {
        "store_name": store_name,
        "cctv_id": cctv_id,
        "roi_name": roi_name,
        "decision": decision,
        "item_type": item_type,
        "cleanliness_mode": cleanliness_mode,
        "cleanliness_decision": cleanliness_decision,
        "cleanliness_final_stage": cleanliness_final_stage,
    }
    pop_filters = {
        "store_name": store_name,
        "cctv_id": cctv_id,
        "roi_name": roi_name,
        "decision": decision,
        "item_type": item_type,
    }
    cleanliness_filters = {
        "store_name": store_name,
        "cctv_id": cctv_id,
        "roi_name": roi_name,
        "mode": cleanliness_mode,
        "decision": cleanliness_decision,
        "final_stage": cleanliness_final_stage,
    }
    records = [with_human_clear_ratio(row) for row in fetch_results(pop_filters)]
    latest_pop = [with_human_clear_ratio(row) for row in fetch_latest_by_roi(roi_name if roi_name else "POP")]
    cleanliness_records = fetch_cleanliness_results(cleanliness_filters)
    cleanliness_store_summary = fetch_cleanliness_store_summary(cleanliness_filters)
    options = merge_report_filter_options(fetch_filter_options(), fetch_cleanliness_filter_options())
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "request": request,
            "title": "Reports",
            "records": records,
            "latest_pop": latest_pop,
            "cleanliness_records": cleanliness_records,
            "cleanliness_store_summary": cleanliness_store_summary,
            "options": options,
            "filters": filters,
        },
    )


@app.get("/cleanliness-reports")
def cleanliness_reports_page() -> RedirectResponse:
    return RedirectResponse(url="/reports", status_code=303)
