import os
from pathlib import Path

from dotenv import dotenv_values


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DOTENV_VALUES = {
    str(key).lstrip("\ufeff"): value
    for key, value in dotenv_values(BASE_DIR / ".env").items()
    if key is not None
}


def env_text(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None and value.strip():
        return value.strip()

    dotenv_value = DOTENV_VALUES.get(name)
    if isinstance(dotenv_value, str) and dotenv_value.strip():
        return dotenv_value.strip()

    return default


def env_int(name: str, default: int) -> int:
    raw_value = env_text(name)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw_value = env_text(name)
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default

DATA_DIR = BASE_DIR / "data"
ROI_CONFIG_DIR = DATA_DIR / "roi_configs"
REFERENCE_IMAGE_DIR = DATA_DIR / "reference_images"
UPLOAD_DIR = DATA_DIR / "uploads"
ANALYSIS_CROP_DIR = DATA_DIR / "analysis_crops"
PERSON_MASK_DIR = DATA_DIR / "person_masks"
POSTER_TEMPLATE_DIR = DATA_DIR / "poster_templates"
TEST_DATA_DIR = DATA_DIR / "test_data"
MOBILE_VIDEO_DIR = DATA_DIR / "mobile_videos"
STORE_CATALOG_PATH = DATA_DIR / "stores.json"
DB_PATH = DATA_DIR / "mvp1.sqlite3"

VISIBILITY_THRESHOLD = 0.60
OCCLUSION_THRESHOLD = 0.35
DARKNESS_THRESHOLD = 0.22
BRIGHTNESS_MISMATCH_THRESHOLD = 0.18
PERSISTENT_MISMATCH_SECONDS = 3.0
UNKNOWN_CONFIDENCE_THRESHOLD = 0.62
VISIBILITY_SAMPLE_SECONDS = 0.1
MAX_VISIBILITY_SAMPLE_STEP_FRAMES = 24
OPENAI_API_KEY = env_text("OPENAI_API_KEY") or None
OPENAI_MODEL = env_text("OPENAI_MODEL", "gpt-4.1-mini")
AUTH_TOKEN_SECRET = env_text("AUTH_TOKEN_SECRET", "mvp1-local-dev-secret")
AUTH_TOKEN_TTL_SECONDS = env_int("AUTH_TOKEN_TTL_SECONDS", 60 * 60 * 12)
MOBILE_VIDEO_UPLOAD_INTERVAL_SECONDS = env_int("MOBILE_VIDEO_UPLOAD_INTERVAL_SECONDS", 60)

for path in (
    DATA_DIR,
    ROI_CONFIG_DIR,
    REFERENCE_IMAGE_DIR,
    UPLOAD_DIR,
    ANALYSIS_CROP_DIR,
    PERSON_MASK_DIR,
    POSTER_TEMPLATE_DIR,
    TEST_DATA_DIR,
    MOBILE_VIDEO_DIR,
):
    path.mkdir(parents=True, exist_ok=True)
