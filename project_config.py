"""Shared configuration and small runtime helpers for both recognition apps."""

from __future__ import annotations

import configparser
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    camera_stream_url: str
    camera_capture_url: str
    controller_url: str
    dataset_dir: Path
    face_distance_threshold: float
    similarity_threshold: float
    capture_timeout_seconds: float


def load_settings(path=None, environ=None):
    env = os.environ if environ is None else environ
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT / "config.ini" if path is None else path, encoding="utf-8-sig")

    def setting(section, key, variable, default):
        return env.get(variable, cfg.get(section, key, fallback=default)).strip()

    def http_url(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Device URLs must start with http:// or https://")
        return value

    stream = http_url(setting("devices", "camera_stream_url", "ESP32_CAM_URL", "http://192.168.1.50:81/stream"))
    capture = http_url(setting("devices", "camera_capture_url", "ESP32_CAM_CAPTURE_URL", "http://192.168.1.50/capture"))
    controller = http_url(setting("devices", "controller_url", "ESP32_CONTROL_URL", "http://192.168.1.51")).rstrip("/")
    dataset = Path(setting("paths", "dataset_dir", "FACE_DATASET_DIR", "dataset")).expanduser()
    if not dataset.is_absolute():
        dataset = ROOT / dataset
    distance = float(setting("recognition", "face_distance_threshold", "FACE_DISTANCE_THRESHOLD", "0.50"))
    similarity = float(setting("recognition", "similarity_threshold", "FACE_SIMILARITY_THRESHOLD", "0.50"))
    timeout = float(setting("recognition", "capture_timeout_seconds", "FACE_CAPTURE_TIMEOUT", "30"))
    if not 0 < distance <= 1 or not 0 < similarity <= 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Recognition thresholds must be in (0, 1]; capture timeout must be positive.")
    return Settings(stream, capture, controller, dataset.resolve(), distance, similarity, timeout)


def validate_identity(value):
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", value):
        raise ValueError("Use 1-40 ASCII letters, digits, underscores or hyphens; start with a letter or digit.")
    return value


def dataset_identity(filename):
    stem = Path(filename).stem
    base, separator, pose = stem.rpartition("_")
    return base if separator and pose.lower() in {"front", "left", "right"} else stem


def best_distance_match(distances, names, tolerance):
    if not names or len(distances) != len(names):
        return None
    candidates = [(float(d), name) for d, name in zip(distances, names) if math.isfinite(float(d))]
    if not candidates:
        return None
    distance, name = min(candidates, key=lambda item: item[0])
    return name if distance <= tolerance else None


def notify_controller(url, params=None, timeout=2):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def reset_registration(state):
    state["temp_reg_name"] = ""
    state["reg_step"] = 0


SETTINGS = load_settings()
