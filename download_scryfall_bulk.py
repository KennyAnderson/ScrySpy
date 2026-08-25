#!/usr/bin/env python3
"""Download Scryfall's current All Cards gzip-compressed JSONL bulk feed.

After decompression, each line is one Scryfall Card object. Each card's
``prices`` field contains the price data provided by Scryfall at download time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.scryfall.com/bulk-data/all-cards"
USER_AGENT = "ScrySpy bulk downloader/1.0 (local data import)"
CHUNK_SIZE = 1024 * 1024  # 1 MiB


def request(url: str):
    """Open a Scryfall request with the headers Scryfall asks API users to send."""
    return urlopen(
        Request(
            url,
            headers={
                "Accept": "application/json;q=0.9,*/*;q=0.8",
                "User-Agent": USER_AGENT,
            },
        ),
        timeout=60,
    )


def get_metadata() -> dict:
    with request(API_URL) as response:
        return json.load(response)


def format_bytes(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MiB"


def download(metadata: dict, output_dir: Path, overwrite: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    updated = metadata["updated_at"].replace(":", "-")
    destination = output_dir / f"scryfall-all-cards-{updated}.jsonl.gz"

    if destination.exists() and not overwrite:
        print(f"Already downloaded: {destination}")
        return destination

    expected_size = metadata.get("compressed_size")
    expected_sha256 = metadata.get("compressed_sha256")
    fd, temp_name = tempfile.mkstemp(prefix=".scryfall-download-", suffix=".part", dir=output_dir)
    downloaded = 0
    digest = hashlib.sha256()

    try:
        with os.fdopen(fd, "wb") as temp_file, request(metadata["jsonl_download_uri"]) as response:
            while chunk := response.read(CHUNK_SIZE):
                temp_file.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if expected_size:
                    print(
                        f"\rDownloaded {format_bytes(downloaded)} / {format_bytes(expected_size)}",
                        end="",
                        flush=True,
                    )
        print()

        if expected_size is not None and downloaded != expected_size:
            raise RuntimeError(f"Size mismatch: received {downloaded:,} bytes, expected {expected_size:,}")
        if expected_sha256 and digest.hexdigest() != expected_sha256:
            raise RuntimeError("SHA-256 mismatch; the incomplete/corrupt download was discarded")

        os.replace(temp_name, destination)  # Atomic once fully verified.
        return destination
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_manifest(metadata: dict, data_file: Path) -> Path:
    manifest = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "data_file": data_file.name,
        "bulk_data": metadata,
        "notes": "Card printings are in data_file; price fields are card.prices.",
    }
    path = data_file.parent / "scryfall-all-cards-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Scryfall's All Cards bulk JSON.")
    parser.add_argument("output_dir", nargs="?", default="data", help="Directory for the JSON and manifest (default: data)")
    parser.add_argument("--overwrite", action="store_true", help="Replace a matching existing download")
    args = parser.parse_args()

    try:
        metadata = get_metadata()
        print(f"Bulk dataset: {metadata['name']}")
        advertised_size = metadata.get("compressed_size")
        size_message = format_bytes(advertised_size) if advertised_size is not None else "not provided"
        print(f"Updated: {metadata['updated_at']}; advertised size: {size_message}")
        data_file = download(metadata, Path(args.output_dir), args.overwrite)
        manifest = write_manifest(metadata, data_file)
    except (HTTPError, URLError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved card data: {data_file}")
    print(f"Saved metadata:  {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
