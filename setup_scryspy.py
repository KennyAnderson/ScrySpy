#!/usr/bin/env python3
"""Download, unpack, and launch ScrySpy in one command."""

from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def newest_bulk_file(data_dir: Path) -> Path:
    files = sorted(data_dir.glob("scryfall-all-cards-*.jsonl.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("The downloader did not produce a .jsonl.gz file.")
    return files[0]


def unpack(source: Path) -> Path:
    destination = source.with_suffix("")
    # A few Windows archive tools create a directory with the desired output
    # filename. Avoid overwriting it and use a clear alternate file name.
    if destination.is_dir():
        destination = destination.with_name(destination.stem + "-unpacked.jsonl")
    if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
        print(f"Already unpacked: {destination}")
        return destination

    descriptor, temporary_name = tempfile.mkstemp(prefix=".scryfall-unpack-", suffix=".part", dir=source.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, gzip.open(source, "rb") as compressed:
            while chunk := compressed.read(1024 * 1024):
                output.write(chunk)
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    print(f"Unpacked: {destination}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Download, unpack, and start the ScrySpy card viewer.")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_DIR / "data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    subprocess.run(
        [sys.executable, str(PROJECT_DIR / "download_scryfall_bulk.py"), str(args.data_dir)],
        check=True,
    )
    jsonl_file = unpack(newest_bulk_file(args.data_dir))
    command = [sys.executable, str(PROJECT_DIR / "card_viewer.py"), str(jsonl_file), "--port", str(args.port)]
    if args.no_browser:
        command.append("--no-browser")
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
