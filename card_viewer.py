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
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_lower TEXT NOT NULL, lang TEXT NOT NULL,
            set_code TEXT, set_name TEXT, collector_number TEXT, type_line TEXT,
            oracle_text TEXT, mana_cost TEXT, cmc REAL, colors TEXT, color_identity TEXT,
            keywords TEXT, produced_mana TEXT, power TEXT, toughness TEXT,
            loyalty TEXT, rarity TEXT, released_at TEXT, artist TEXT, prices TEXT, legalities TEXT,
            scryfall_uri TEXT, image_normal TEXT, image_large TEXT
        );
        CREATE INDEX cards_name_lower ON cards(name_lower);
    """)
    insert = """INSERT OR REPLACE INTO cards VALUES (
        :id, :name, :name_lower, :lang, :set_code, :set_name, :collector_number,
        :type_line, :oracle_text, :mana_cost, :cmc, :colors, :color_identity,
        :keywords, :produced_mana, :power, :toughness, :loyalty, :rarity,
        :released_at, :artist, :prices, :legalities, :scryfall_uri, :image_normal, :image_large
    )"""
    batch: list[dict] = []
    with path.open("r", encoding="utf-8") as source:
        for count, line in enumerate(source, start=1):
            card = json.loads(line)
            batch.append({
                "id": card["id"], "name": card["name"], "name_lower": card["name"].lower(), "lang": card["lang"],
                "set_code": card.get("set"), "set_name": card.get("set_name"),
                "collector_number": card.get("collector_number"), "type_line": card.get("type_line"),
                "oracle_text": card.get("oracle_text"), "mana_cost": card.get("mana_cost"), "cmc": card.get("cmc"),
                "colors": text(card.get("colors")), "color_identity": text(card.get("color_identity")),
                "keywords": text(card.get("keywords")), "produced_mana": text(card.get("produced_mana")),
                "power": card.get("power"), "toughness": card.get("toughness"), "loyalty": card.get("loyalty"),
                "rarity": card.get("rarity"), "released_at": card.get("released_at"),
                "artist": card.get("artist"), "prices": text(card.get("prices")), "legalities": text(card.get("legalities")),
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
body{font:16px system-ui;margin:0;background:#14171b;color:#edf1f5}main{display:grid;grid-template-columns:360px minmax(0,1fr);min-height:100vh}main.deck-mode{grid-template-columns:360px minmax(0,1fr) 350px}aside{padding:20px;background:#1d232b}.search-panel{box-sizing:border-box;height:100vh;position:sticky;top:0;display:flex;flex-direction:column}.search-panel h1{margin-top:0}input,select{box-sizing:border-box;width:100%;padding:11px;font-size:16px;margin-bottom:9px}button{width:100%;text-align:left;padding:10px;margin-top:7px;background:#283440;color:inherit;border:0;border-radius:5px;cursor:pointer}button:hover{background:#38516a}.secondary{width:auto;display:inline-block}.result{padding:8px 0;border-bottom:1px solid #34404c}.result button{margin:0}.actions{display:flex;gap:6px}.actions button{flex:1;text-align:center}.muted{color:#aab6c3;font-size:.9em}#results{overflow-y:auto;min-height:0;flex:1;padding-right:4px}article{padding:32px;max-width:850px}.card-visual{float:right;width:330px;margin:0 0 18px 28px}.card-visual img{display:block;width:100%;max-height:460px;object-fit:contain;border-radius:12px}.card-visual .actions button{margin-top:7px}pre{white-space:pre-wrap;background:#202730;padding:14px;border-radius:7px}a{color:#8ecbff}.hidden{display:none}#deck-panel{background:#1d232b;border-left:1px solid #34404c;padding:20px}.deck-card{padding:10px 0;border-bottom:1px solid #34404c}.deck-card b{display:block}@media(max-width:900px){main.deck-mode{grid-template-columns:320px minmax(0,1fr)}#deck-panel{grid-column:1/-1;border-left:0;border-top:1px solid #34404c}}@media(max-width:700px){main,main.deck-mode{display:block}.search-panel{height:auto;position:static}.card-visual{float:none;width:100%;margin:0 0 18px}.card-visual img{width:auto;max-width:100%}#results{max-height:50vh}}
</style><main id=app><aside class=search-panel><h1>ScrySpy</h1><button class=secondary id=deck-toggle onclick="toggleDeckMode()">Build a deck</button><select id=language onchange="search(query.value)"><option value="en" selected>English (EN)</option><option value="">All languages</option><option value="es">Spanish (ES)</option><option value="fr">French (FR)</option><option value="de">German (DE)</option><option value="it">Italian (IT)</option><option value="pt">Portuguese (PT)</option><option value="ja">Japanese (JA)</option><option value="ko">Korean (KO)</option><option value="ru">Russian (RU)</option><option value="zhs">Simplified Chinese (ZHS)</option><option value="zht">Traditional Chinese (ZHT)</option></select><input id=query autofocus placeholder="Search cards, rules text, types…" oninput="search(this.value)"><select id=cardType onchange="search(query.value)"><option value="">Any card type</option><option>Creature</option><option>Instant</option><option>Sorcery</option><option>Artifact</option><option>Enchantment</option><option>Planeswalker</option><option>Land</option></select><select id=colorFilter onchange="search(query.value)"><option value="">Any color identity</option><option value="W">White identity</option><option value="U">Blue identity</option><option value="B">Black identity</option><option value="R">Red identity</option><option value="G">Green identity</option><option value="colorless">Colorless identity</option></select><select id=maxCmc onchange="search(query.value)"><option value="">Any mana value</option><option value="1">Mana value 1 or less</option><option value="2">Mana value 2 or less</option><option value="3">Mana value 3 or less</option><option value="4">Mana value 4 or less</option><option value="5">Mana value 5 or less</option></select><select id=rarityFilter onchange="search(query.value)"><option value="">Any rarity</option><option value="common">Common</option><option value="uncommon">Uncommon</option><option value="rare">Rare</option><option value="mythic">Mythic</option></select><select id=formatFilter onchange="search(query.value)"><option value="">Any format</option><option value="commander">Commander legal</option><option value="standard">Standard legal</option><option value="pioneer">Pioneer legal</option><option value="modern">Modern legal</option><option value="legacy">Legacy legal</option><option value="vintage">Vintage legal</option><option value="pauper">Pauper legal</option></select><p class=muted id=status>English prints only. Type to search.</p><div id=results></div></aside><article id=card><h2>Select a card</h2><p>Search by name, rules text, or type line.</p></article><aside id=deck-panel class=hidden><h2>Deck</h2><p id=deck-total><b>Total: $0.00 USD</b></p><p class=muted id=deck-count>0 cards</p><div id=deck-list><p class=muted>No cards added.</p></div></aside></main>
<script>
const appElement=document.getElementById('app'),deckPanel=document.getElementById('deck-panel'),deckToggle=document.getElementById('deck-toggle'),deckTotal=document.getElementById('deck-total'),deckCount=document.getElementById('deck-count'),deckList=document.getElementById('deck-list');let timer,deckMode=false,deck={}; function search(q){clearTimeout(timer);timer=setTimeout(async()=>{let filters='&lang='+encodeURIComponent(language.value)+'&type='+encodeURIComponent(cardType.value)+'&color='+encodeURIComponent(colorFilter.value)+'&max_cmc='+encodeURIComponent(maxCmc.value)+'&rarity='+encodeURIComponent(rarityFilter.value)+'&format='+encodeURIComponent(formatFilter.value),r=await fetch('/api/search?q='+encodeURIComponent(q)+filters),d=await r.json(),out=document.querySelector('#results'),scope=language.value?language.options[language.selectedIndex].text:'All languages';status.textContent=d.length?d.length+' results · '+scope:'No results · '+scope;out.innerHTML=d.map(x=>`<div class=result><button onclick="showCard('${x.id}')"><b>${esc(x.name)}</b><br><span class=muted>${esc(x.set_name||'')} · ${esc(x.collector_number||'')} · ${esc(x.lang||'')}</span></button>${deckMode?`<div class=actions><button onclick="addCard('${x.id}',1)">Add 1</button><button onclick="addCard('${x.id}',4)">Add 4</button></div>`:''}</div>`).join('')},150)}
function toggleDeckMode(){deckMode=!deckMode;appElement.classList.toggle('deck-mode',deckMode);deckPanel.classList.toggle('hidden',!deckMode);deckToggle.textContent=deckMode?'Exit deck builder':'Build a deck';search(query.value)}
async function addCard(id,quantity){let item=deck[id];if(!item){let response=await fetch('/api/card?id='+id);item={card:await response.json(),quantity:0};deck[id]=item}item.quantity+=quantity;renderDeck()}
function removeCard(id,quantity){deck[id].quantity-=quantity;if(deck[id].quantity<=0)delete deck[id];renderDeck()}
function renderDeck(){let items=Object.values(deck),count=items.reduce((n,x)=>n+x.quantity,0),total=items.reduce((n,x)=>n+(Number(JSON.parse(x.card.prices).usd)||0)*x.quantity,0);deckTotal.innerHTML='<b>Total: $'+total.toFixed(2)+' USD</b>';deckCount.textContent=count+' card'+(count===1?'':'s');deckList.innerHTML=items.length?items.map(x=>{let p=Number(JSON.parse(x.card.prices).usd)||0;return `<div class=deck-card><b>${esc(x.card.name)}</b><span class=muted>${esc(x.card.set_name||'')} · $${p.toFixed(2)} each</span><div class=actions><button onclick="removeCard('${x.card.id}',1)">−1</button><button onclick="addCard('${x.card.id}',1)">+1</button><button onclick="addCard('${x.card.id}',4)">+4</button><button onclick="removeCard('${x.card.id}',4)">−4</button></div><span class=muted>Quantity: ${x.quantity} · $${(p*x.quantity).toFixed(2)}</span></div>`}).join(''):'<p class=muted>No cards added.</p>'}
async function showCard(id){window.scrollTo({top:0,behavior:'smooth'});let c=await (await fetch('/api/card?id='+id)).json(), prices=JSON.parse(c.prices), list=(x)=>JSON.parse(x||'[]').join(', ')||'—',visual=c.image_normal?`<div class=card-visual><img src="${c.image_normal}" alt="${esc(c.name)}">${deckMode?`<div class=actions><button onclick="addCard('${c.id}',1)">Add 1</button><button onclick="addCard('${c.id}',4)">Add 4</button></div>`:''}</div>`:''; card.innerHTML=`${visual}<h1>${esc(c.name)}</h1><p class=muted>${esc(c.set_name||'')} (${esc(c.set_code||'')}) #${esc(c.collector_number||'')} · ${esc(c.lang.toUpperCase())}</p><p><b>${esc(c.mana_cost||'')}</b> ${esc(c.type_line||'')} · Mana value ${esc(c.cmc)}</p><pre>${esc(c.oracle_text||'')}</pre><p><b>Colors:</b> ${list(c.colors)} &nbsp; <b>Identity:</b> ${list(c.color_identity)}<br><b>Keywords:</b> ${list(c.keywords)}<br><b>Power/Toughness:</b> ${esc(c.power||'—')}/${esc(c.toughness||'—')} &nbsp; <b>Loyalty:</b> ${esc(c.loyalty||'—')}<br><b>Artist:</b> ${esc(c.artist||'—')} &nbsp; <b>Released:</b> ${esc(c.released_at||'—')}</p><h3>Prices</h3><pre>${esc(JSON.stringify(prices,null,2))}</pre>${c.scryfall_uri?`<p><a target="_blank" href="${c.scryfall_uri}">Open on Scryfall</a></p>`:''}`}
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
                language = params.get("lang", ["en"])[0]
                sql = "SELECT id,name,set_name,collector_number,lang FROM cards WHERE (name_lower LIKE ? OR lower(COALESCE(oracle_text,'')) LIKE ? OR lower(COALESCE(type_line,'')) LIKE ?)"
                values = [like, like, like]
                if language:
                    sql += " AND lang = ?"
                    values.append(language)
                card_type = params.get("type", [""])[0].lower()
                if card_type:
                    sql += " AND lower(COALESCE(type_line,'')) LIKE ?"
                    values.append(f"%{card_type}%")
                color = params.get("color", [""])[0]
                if color == "colorless":
                    sql += " AND color_identity = '[]'"
                elif color:
                    sql += " AND color_identity LIKE ?"
                    values.append(f'%"{color}"%')
                max_cmc = params.get("max_cmc", [""])[0]
                if max_cmc.isdigit():
                    sql += " AND cmc <= ?"
                    values.append(int(max_cmc))
                rarity = params.get("rarity", [""])[0]
                if rarity:
                    sql += " AND rarity = ?"
                    values.append(rarity)
                game_format = params.get("format", [""])[0]
                if game_format:
                    sql += " AND legalities LIKE ?"
                    values.append(f'%"{game_format}": "legal"%')
                rows = db.execute(sql + " ORDER BY CASE WHEN name_lower = ? THEN 0 ELSE 1 END, name LIMIT 50", values + [query]).fetchall()
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
