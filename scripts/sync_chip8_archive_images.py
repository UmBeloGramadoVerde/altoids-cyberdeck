from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


RAW_BASE_URL = "https://raw.githubusercontent.com/JohnEarnest/chip8Archive/master/src"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download CHIP-8 archive preview images referenced by programs.json.")
    parser.add_argument("--archive-dir", default="roms/chip8/archive", help="Local chip8Archive directory")
    parser.add_argument("--base-url", default=RAW_BASE_URL, help="Raw GitHub URL for chip8Archive/src")
    parser.add_argument("--limit", type=int, default=0, help="Stop after downloading this many missing images")
    return parser.parse_args()


def image_entries(programs_path: Path) -> list[tuple[str, str]]:
    programs = json.loads(programs_path.read_text(encoding="utf-8"))
    entries: list[tuple[str, str]] = []
    for key, value in programs.items():
        if not isinstance(value, dict):
            continue
        images = value.get("images")
        if not isinstance(images, list):
            continue
        for image_name in images:
            if isinstance(image_name, str) and image_name:
                entries.append((str(key), image_name))
    return entries


def image_url(base_url: str, key: str, image_name: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(key)}/{quote(image_name)}"


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    with urlopen(url, timeout=30) as response:
        tmp_path.write_bytes(response.read())
    tmp_path.replace(destination)


def main() -> int:
    args = parse_args()
    archive_dir = Path(args.archive_dir)
    programs_path = archive_dir / "programs.json"
    if not programs_path.exists():
        raise SystemExit(f"missing {programs_path}")

    downloaded = 0
    skipped = 0
    failed: list[str] = []
    for key, image_name in image_entries(programs_path):
        destination = archive_dir / "src" / key / image_name
        if destination.exists():
            skipped += 1
            continue
        url = image_url(args.base_url, key, image_name)
        try:
            download(url, destination)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed.append(f"{key}/{image_name}: {exc}")
            continue
        downloaded += 1
        print(f"downloaded {key}/{image_name}")
        if args.limit and downloaded >= args.limit:
            break

    print(f"downloaded={downloaded} skipped={skipped} failed={len(failed)}")
    for item in failed[:20]:
        print(f"failed {item}")
    if len(failed) > 20:
        print(f"... {len(failed) - 20} more failures")
    return 1 if failed and downloaded == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
