"""Save a single enrollment snapshot; use the Streamlit UI for live three-pose capture."""

import argparse
from urllib.request import urlopen

from project_config import SETTINGS, validate_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="ASCII identity, e.g. User01")
    parser.add_argument("--pose", choices=("front", "left", "right"), default="front")
    parser.add_argument("--yes", action="store_true", help="Capture immediately without a positioning prompt")
    args = parser.parse_args()
    try:
        name = validate_identity(args.name)
    except ValueError as error:
        parser.error(str(error))
    if not args.yes:
        input(f"Face the camera ({args.pose}), then press Enter to capture: ")
    with urlopen(SETTINGS.camera_capture_url, timeout=10) as response:
        image = response.read(10 * 1024 * 1024 + 1)
        if response.headers.get_content_type() != "image/jpeg" or not image.startswith(b"\xff\xd8") or len(image) > 10 * 1024 * 1024:
            raise RuntimeError("The camera did not return a valid-sized JPEG response.")
    SETTINGS.dataset_dir.mkdir(parents=True, exist_ok=True)
    path = SETTINGS.dataset_dir / f"{name}_{args.pose}.jpg"
    path.write_bytes(image)
    print(f"Saved {path}. Restart the recognition app to reload externally added images.")


if __name__ == "__main__":
    main()
