#!/usr/bin/env python3
"""A small local Scryfall card browser backed by an in-memory SQLite index."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_DATA_DIR = Path("data")


def find_jsonl(data_dir: Path) -> Path:
    # Some archive tools create a directory named after the archive, so search
    # below data/ as well as directly inside it.
    files = sorted(
        (path for path in data_dir.rglob("scryfall-all-cards-*.jsonl") if path.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No unzipped .jsonl file found in {data_dir.resolve()}")
    return files[0]


def text(value: object) -> str:
    """Serialize list-like source fields compactly for SQLite storage."""
    return json.dumps(value, ensure_ascii=False) if value is not None else "[]"


def image_url(card: dict, size: str) -> str | None:
    # Multi-face cards store images on their individual faces.
    if card.get("image_uris"):
        return card["image_uris"].get(size)
    faces = card.get("card_faces", [])
    if faces and faces[0].get("image_uris"):
        return faces[0]["image_uris"].get(size)
    return None


def load_cards(path: Path, max_cards: int | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE cards (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_lower TEXT NOT NULL,
            set_code TEXT, set_name TEXT, collector_number TEXT, type_line TEXT,
            oracle_text TEXT, mana_cost TEXT, colors TEXT, color_identity TEXT,
            keywords TEXT, produced_mana TEXT, power TEXT, toughness TEXT,
            loyalty TEXT, rarity TEXT, released_at TEXT, artist TEXT, prices TEXT,
            scryfall_uri TEXT, image_normal TEXT, image_large TEXT
        );
        CREATE INDEX cards_name_lower ON cards(name_lower);
    """)
    insert = """INSERT OR REPLACE INTO cards VALUES (
        :id, :name, :name_lower, :set_code, :set_name, :collector_number,
        :type_line, :oracle_text, :mana_cost, :colors, :color_identity,
        :keywords, :produced_mana, :power, :toughness, :loyalty, :rarity,
        :released_at, :artist, :prices, :scryfall_uri, :image_normal, :image_large
    )"""
    batch: list[dict] = []
    with path.open("r", encoding="utf-8") as source:
        for count, line in enumerate(source, start=1):
            card = json.loads(line)
            batch.append({
                "id": card["id"], "name": card["name"], "name_lower": card["name"].lower(),
                "set_code": card.get("set"), "set_name": card.get("set_name"),
                "collector_number": card.get("collector_number"), "type_line": card.get("type_line"),
                "oracle_text": card.get("oracle_text"), "mana_cost": card.get("mana_cost"),
                "colors": text(card.get("colors")), "color_identity": text(card.get("color_identity")),
                "keywords": text(card.get("keywords")), "produced_mana": text(card.get("produced_mana")),
                "power": card.get("power"), "toughness": card.get("toughness"), "loyalty": card.get("loyalty"),
                "rarity": card.get("rarity"), "released_at": card.get("released_at"),
                "artist": card.get("artist"), "prices": text(card.get("prices")),
                "scryfall_uri": card.get("scryfall_uri"),
                "image_normal": image_url(card, "normal"), "image_large": image_url(card, "large"),
            })
            if len(batch) == 1_000:
                db.executemany(insert, batch)
                batch.clear()
            if count % 25_000 == 0:
                print(f"Indexed {count:,} cards", flush=True)
            if max_cards and count >= max_cards:
                break
    if batch:
        db.executemany(insert, batch)
    db.commit()
    total = db.execute("SELECT count(*) FROM cards").fetchone()[0]
    print(f"Ready: indexed {total:,} card printings from {path.name}")
    return db


PAGE = r"""<!doctype html><meta charset="utf-8"><title>ScrySpy Card Viewer</title>
<style>
body{font:16px system-ui;margin:0;background:#14171b;color:#edf1f5}main{display:grid;grid-template-columns:360px 1fr;min-height:100vh}aside{padding:20px;background:#1d232b}input{box-sizing:border-box;width:100%;padding:11px;font-size:16px}button{width:100%;text-align:left;padding:10px;margin-top:7px;background:#283440;color:inherit;border:0;border-radius:5px;cursor:pointer}button:hover{background:#38516a}.muted{color:#aab6c3;font-size:.9em}article{padding:32px;max-width:850px}img{max-width:330px;max-height:460px;float:right;margin:0 0 18px 28px;border-radius:12px}pre{white-space:pre-wrap;background:#202730;padding:14px;border-radius:7px}a{color:#8ecbff}@media(max-width:700px){main{display:block}img{float:none;margin:0;max-width:100%}}
</style><main><aside><h1>ScrySpy</h1><input autofocus placeholder="Search cards, rules text, types…" oninput="search(this.value)"><p class=muted id=status>Type to search.</p><div id=results></div></aside><article id=card><h2>Select a card</h2><p>Search by name, rules text, or type line.</p></article></main>
<script>
let timer; function search(q){clearTimeout(timer);timer=setTimeout(async()=>{let r=await fetch('/api/search?q='+encodeURIComponent(q)),d=await r.json(),out=document.querySelector('#results');status.textContent=d.length?d.length+' results':'No results';out.innerHTML=d.map(x=>`<button onclick="showCard('${x.id}')"><b>${esc(x.name)}</b><br><span class=muted>${esc(x.set_name||'')} · ${esc(x.collector_number||'')}</span></button>`).join('')},150)}
async function showCard(id){let c=await (await fetch('/api/card?id='+id)).json(), prices=JSON.parse(c.prices), list=(x)=>JSON.parse(x||'[]').join(', ')||'—'; card.innerHTML=`${c.image_normal?`<img src="${c.image_normal}" alt="${esc(c.name)}">`:''}<h1>${esc(c.name)}</h1><p class=muted>${esc(c.set_name||'')} (${esc(c.set_code||'')}) #${esc(c.collector_number||'')}</p><p><b>${esc(c.mana_cost||'')}</b> ${esc(c.type_line||'')}</p><pre>${esc(c.oracle_text||'')}</pre><p><b>Colors:</b> ${list(c.colors)} &nbsp; <b>Identity:</b> ${list(c.color_identity)}<br><b>Keywords:</b> ${list(c.keywords)}<br><b>Power/Toughness:</b> ${esc(c.power||'—')}/${esc(c.toughness||'—')} &nbsp; <b>Loyalty:</b> ${esc(c.loyalty||'—')}<br><b>Artist:</b> ${esc(c.artist||'—')} &nbsp; <b>Released:</b> ${esc(c.released_at||'—')}</p><h3>Prices</h3><pre>${esc(JSON.stringify(prices,null,2))}</pre>${c.scryfall_uri?`<p><a target="_blank" href="${c.scryfall_uri}">Open on Scryfall</a></p>`:''}`}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script>"""


def make_handler(db: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send_json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            request = urlparse(self.path)
            params = parse_qs(request.query)
            if request.path == "/":
                body = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            if request.path == "/api/search":
                query = params.get("q", [""])[0].strip().lower()
                if len(query) < 2: return self.send_json([])
                like = f"%{query}%"
                rows = db.execute("SELECT id,name,set_name,collector_number FROM cards WHERE name_lower LIKE ? OR lower(COALESCE(oracle_text,'')) LIKE ? OR lower(COALESCE(type_line,'')) LIKE ? ORDER BY name LIMIT 50", (like, like, like)).fetchall()
                return self.send_json([dict(row) for row in rows])
            if request.path == "/api/card":
                row = db.execute("SELECT * FROM cards WHERE id = ?", (params.get("id", [""])[0],)).fetchone()
                return self.send_json(dict(row) if row else {"error": "Card not found"}, 200 if row else 404)
            self.send_error(404)
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="View a Scryfall JSONL export locally.")
    parser.add_argument("jsonl_file", nargs="?", type=Path, help="Unzipped Scryfall JSONL file (defaults to newest data file)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--max-cards", type=int, help="Only index this many records (useful for testing)")
    args = parser.parse_args()
    path = args.jsonl_file or find_jsonl(DEFAULT_DATA_DIR)
    db = load_cards(path, args.max_cards)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(db))
    url = f"http://127.0.0.1:{args.port}"
    print(f"Open {url} (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")
    finally: server.server_close(); db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
