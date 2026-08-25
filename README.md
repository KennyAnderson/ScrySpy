# ScrySpy

ScrySpy downloads Scryfall's complete **All Cards** dataset and provides a
simple local card viewer. It uses only Python's standard library.

## Quick start

With Python 3.10+ installed, run:

```powershell
python .\setup_scryspy.py
```

This one command downloads the current Scryfall bulk export, unpacks the gzip
JSONL data, builds a temporary in-memory SQLite search index, and opens the
viewer at `http://127.0.0.1:8765`. The first launch takes time and disk space:
the compressed download is roughly 400 MB and the unpacked JSONL is roughly
2.9 GB. Press `Ctrl+C` in the terminal to stop the viewer.

The application shows the card image via Scryfall's hosted image URL; the bulk
dataset itself does not download image files.

## Download only

To download Scryfall's **All Cards** bulk dataset (one JSON object per card
printing, with `prices` included on each card) without launching the viewer:

```powershell
python .\download_scryfall_bulk.py .\data
```

The script uses only Python's standard library. It writes:

- `scryfall-all-cards-<timestamp>.jsonl.gz` — the complete gzip-compressed
  feed, with one card object per JSON line.
- `scryfall-all-cards-manifest.json` — source metadata, including the dataset
  timestamp and SHA-256 from Scryfall.

Downloads go to a temporary `.part` file and are only renamed after any byte
size and SHA-256 values supplied by Scryfall match. Re-run the same command to
keep an already-downloaded version, or add `--overwrite` to fetch it again.

This deliberately preserves Scryfall's source schema intact; the next import
script can stream the gzip JSONL file into SQLite without loading the entire
dataset into memory.

## Viewer only

After unpacking the bulk `.jsonl.gz` file, start the local viewer directly:

```powershell
python .\card_viewer.py
```

It streams the JSONL file into an in-memory SQLite index, opens
`http://127.0.0.1:8765`, and lets you search card names, rules text, and types.
It displays the card image using the Scryfall image URL included in each record.
Stop the terminal process when finished; the index is intentionally temporary.
