"""Read-only device checks: PIR, JPEG capture and MJPEG response. No servo commands."""

import argparse
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_config import SETTINGS


def probe(url, kind):
    with urlopen(url, timeout=5) as response:
        content_type = response.headers.get_content_type()
        if kind == "pir":
            value = response.read(16).decode().strip()
            if value not in {"0", "1"}:
                raise ValueError("Expected PIR response 0 or 1")
            return f"PIR={value}"
        if kind == "snapshot":
            if content_type != "image/jpeg" or response.read(2) != b"\xff\xd8":
                raise ValueError("Expected a JPEG snapshot")
            return "JPEG response received"
        if content_type != "multipart/x-mixed-replace" or not response.read(128):
            raise ValueError("Expected an MJPEG multipart response")
        return "MJPEG response received"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    failed = False
    for name, url, kind in [
        ("Controller", SETTINGS.controller_url + "/check_pir", "pir"),
        ("Camera capture", SETTINGS.camera_capture_url, "snapshot"),
        ("Camera stream", SETTINGS.camera_stream_url, "stream"),
    ]:
        try:
            print(f"PASS {name}: {probe(url, kind)}")
        except Exception as error:
            failed = True
            print(f"FAIL {name}: {error}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
