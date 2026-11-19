import os, json, csv, sqlite3, requests
from io import BytesIO, StringIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DB = "pokebinder.db"
MODEL = "gemini-3.8-flash"
FLAGS = {"ITA": "🇮🇹 ITA", "JAP": "🇯🇵 JAP", "ENG": "🇬🇧 ENG", "KOR": "🇰🇷 KOR"}

def ask_gemini(contents, search=True):
    tries = [(MODEL, search), ("gemini-3.5-flash-lite", search), ("gemini-3.5-flash-lite", False)]
    for m, use_s in tries:
        try:
            cfg = types.GenerateContentConfig(tools=[{"google_search": {}}]) if use_s else None
            return client.models.generate_content(model=m, contents=contents, config=cfg).text
        except Exception as e:
            if any(k in str(e) for k in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                continue
            raise e
    raise RuntimeError("Server AI momentaneamente occupati. Riprova tra 10 secondi.")

def parse_json(txt, is_list=False):
    try:
        clean = txt.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()
        a, b = ("[", "]") if is_list else ("{", "}")
        start, end = clean.find(a), clean.rfind(b)
        if start != -1 and end != -1:
            clean = clean[start:end + 1]
        return json.loads(clean)
    except Exception as e:
        raise ValueError(f"Errore parsing JSON: {e} | Testo ricevuto: {txt[:100]}")

def cm_search(name, num=""):
    try:
        n = num.split("/")[0].lstrip("0") if num else ""
        q = f'name:"{name.split()[0]}"' + (f' number:{n}' if n else "")
        r = requests.get("https://api.pokemontcg.io/v2/cards", params={"q": q, "pageSize": 5}, headers={"User-Agent": "PokeBinderBot/1.0"}, timeout=8)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            for c in r.json().get("data", []):
                p = c.get("cardmarket", {}).get("prices", {})
                val = p.get("trendPrice") or p.get("averageSellPrice") or p.get("avg7")
                if val:
                    return {"price": float(val), "img": c.get("images", {}).get("large", "")}
    except Exception: pass
    return None

def radar_live():
    pool = []
    try:
        r = requests.get("https://api.pokemontcg.io/v2/cards", params={"q": 'rarity:"Special Illustration Rare" OR rarity:"Illustration Rare"', "pageSize": 30}, headers={"User-Agent": "PokeBinderBot/1.0"}, timeout=8)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            for c in r.json().get("data", []):
                p = c.get("cardmarket", {}).get("prices", {})
                t, a30 = p.get("trendPrice") or p.get("avg7") or 0, p.get("avg30") or 0
                if t >= 10 and a30 >= 5:
                    delta = ((t - a30) / a30) * 100
                    num = f"{c.get('number','')}/{c.get('set',{}).get('printedTotal','')}"
                    pool.append({"name": c["name"], "num": num, "set": c.get("set",{}).get("name",""), "price": t, "a30": a30, "delta": delta, "img": c.get("images",{}).get("large","")})
    except Exception: pass
    
    if len(pool) >= 4:
        pool.sort(key=lambda x: x["delta"], reverse=True)
        rising = pool[:2]
        sleepers = [x for x in pool[2:] if 10 <= x["price"] <= 80][:2] or pool[2:4]
        picks = [dict(x, tag="📈 IN RIALZO") for x in rising] + [dict(x, tag="🔮 SLEEPER") for x in sleepers]
        try:
            summary = "\n".join([f"- {x['name']} ({x['num']}, {x['set']}): Trend {x['price']:.2f}€ vs 30gg {x['a30']:.2f}€ ({x['delta']:+.1f}%)" for x in picks])
            prompt = f'Scrivi una motivazione brevissima in italiano (massimo 12 parole) per ciascuna di queste 4 carte Pokemon basandoti sui dati Cardmarket:\n{summary}\nRispondi SOLO con un oggetto JSON in cui le chiavi sono i numeri da 0 a 3, es: {{"0":"...","1":"...","2":"...","3":"..."}}'
            notes = parse_json(ask_gemini(prompt, search=False))
            for i, x in enumerate(picks): x["reason"] = notes.get(str(i), f"Trend Cardmarket {x['delta']:+.1f}% rispetto alla media.")
        except Exception:
            for x in picks: x["reason"] = f"Trend Cardmarket {x['delta']:+.1f}% rispetto alla media 30gg."
        return picks

    fallback_prompt = (
        'Cerca sul web i trend attuali reali del mercato Pokemon TCG (Cardmarket/TCGPlayer). '
        'Seleziona 4 carte (2 IN RIALZO ORA e 2 SLEEPER con potenziale di crescita imminente). '
        'Rispondi SOLO con un array JSON valido di 4 oggetti: '
        '[{"tag":"📈 IN RIALZO","name":"Nome inglese esatto","num":"215/203","set":"Nome Set","price":125.0,"delta":14.5,"reason":"Motivo breve in italiano"}]'
    )
    txt = ask_gemini(fallback_prompt, search=True)
    picks = parse_json(txt, is_list=True)[:4]
    for x in picks:
        x["price"] = float(str(x.get("price", 0)).replace("€","").replace(",",".").strip() or 0)
        x["delta"] = float(str(x.get("delta", 0)).replace("%","").replace("+","").strip() or 0)
        x["img"] = fetch_img(x["name"], x.get("num", ""))
    return picks

def lowcap_live():
    prompt = (
        'Cerca su Cardmarket e TCGPlayer 4 carte Pokemon TCG di tipo Illustration Rare (AR) o Promo con prezzo attuale COMPRESO TRA 3€ E 25€ '
        'che hanno enormi margini di crescita futuri (artisti ricercati, set out-of-print, forte domanda collezionistica). '
        'Rispondi SOLO con un array JSON valido di 4 oggetti: '
        '[{"name":"Nome inglese","num":"120/198","set":"Nome Set","price":8.50,"reason":"Motivo del potenziale futuro in italiano"}]'
    )
    txt = ask_gemini(prompt, search=True)
    picks = parse_json(txt, is_list=True)[:4]
    for x in picks:
        x["price"] = float(str(x.get("price", 0)).replace("€","").replace(",",".").strip() or 0)
        x["img"] = fetch_img(x["name"], x.get("num", ""))
    return picks

def init_db():
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, set_name TEXT, card_number TEXT, lang TEXT, grade TEXT, paid_price REAL, market_price REAL, photo_file_id TEXT, added_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS wishlist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, target_price REAL, added_at TEXT)")

def add_card(uid, d, paid, pid):
    with sqlite3.connect(DB) as c:
        c.execute("INSERT INTO cards (user_id,name,set_name,card_number,lang,grade,paid_price,market_price,photo_file_id,added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (uid, d.get("name","?"), d.get("set_name","N/D"), d.get("card_number",""), d.get("lang","ENG").upper(), d.get("grade","Raw"), paid, float(d.get("market_price",0)), pid, datetime.now().strftime("%Y-%m-%d")))

def get_cards(uid, flt="all"):
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM cards WHERE user_id=?"
        if flt == "JAP": q += " AND lang='JAP' ORDER BY market_price DESC"
        elif flt == "ITA": q += " AND lang='ITA' ORDER BY market_price DESC"
        elif flt == "graded": q += " AND lower(grade)!='raw' ORDER BY market_price DESC"
        elif flt == "pnl": q += " ORDER BY (market_price - paid_price) DESC"
        else: q += " ORDER BY market_price DESC"
        return [dict(r) for r in c.execute(q, (uid,)).fetchall()]

def del_card(cid, uid):
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM cards WHERE id=? AND user_id=?", (cid, uid))

def update_price(cid, uid, price):
    with sqlite3.connect(DB) as c:
        c.execute("UPDATE cards SET market_price=? WHERE id=? AND user_id=?", (float(price), cid, uid))

def add_wish(uid, name, target):
    with sqlite3.connect(DB) as c:
        c.execute("INSERT INTO wishlist (user_id,name,target_price,added_at) VALUES (?,?,?,?)", (uid, name, float(target), datetime.now().strftime("%Y-%m-%d")))

def get_wish(uid):
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM wishlist WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()]

def del_wish(wid, uid):
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM wishlist WHERE id=? AND user_id=?", (wid, uid))

def fetch_img(name, num):
    cm = cm_search(name, num)
    if cm and cm["img"]: return cm["img"]
    try:
        r = requests.get(f"https://api.tcgdex.net/v2/en/cards?name={name.split()[0]}", timeout=5)
        n = num.split("/")[0].lstrip("0")
        for i in r.json():
            if i.get("image") and (not n or i.get("localId","").lstrip("0") == n): return f"{i['image']}/high.png"
    except Exception: pass
    return "https://images.pokemontcg.io/base1/4_hires.png"

def make_portfolio(cards):
    img = Image.new("RGB", (900, 560), (18, 20, 28))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 20, 880, 540], 24, outline=(212, 175, 55), width=3)
    d.rounded_rectangle([45, 110, 435, 280], 16, fill=(28, 32, 44))
    d.rounded_rectangle([465, 110, 855, 280], 16, fill=(28, 32, 44))
    d.rounded_rectangle([45, 305, 855, 515], 16, fill=(28, 32, 44))
    ft, fs, fb, fm = ImageFont.load_default(30), ImageFont.load_default(18), ImageFont.load_default(38), ImageFont.load_default(22)
    tv, tp = sum(c["market_price"] for c in cards), sum(c["paid_price"] for c in cards)
    pnl = tv - tp
    pct = (pnl / tp * 100) if tp > 0 else 0.0
    col = (72, 219, 132) if pnl >= 0 else (255, 95, 95)
    sgn = "+" if pnl >= 0 else ""
    d.text((48, 45), "POKEBINDER PRO  |  PORTAFOGLIO", fill=(212, 175, 55), font=ft)
    d.text((710, 54), f"{len(cards)} Carte", fill=(170, 175, 190), font=fs)
    d.text((70, 132), "VALORE ATTUALE", fill=(160, 168, 185), font=fs)
    d.text((70, 170), f"EUR {tv:,.2f}", fill=(255, 255, 255), font=fb)
    d.text((70, 230), f"Investito: EUR {tp:,.2f}", fill=(140, 148, 165), font=fs)
    d.text((490, 132), "RENDIMENTO (P&L)", fill=(160, 168, 185), font=fs)
    d.text((490, 170), f"{sgn}EUR {pnl:,.2f}", fill=col, font=fb)
    d.text((490, 230), f"{sgn}{pct:.1f}% sul capitale", fill=col, font=fs)
    d.text((70, 322), "TOP CARTE IN COLLEZIONE", fill=(212, 175, 55), font=fs)
    y = 360
    if not cards: d.text((70, y), "Nessuna carta ancora. Invia foto o numero!", fill=(180, 180, 190), font=fm)
    for i, c in enumerate(cards[:3], 1):
        d.text((70, y), f"{i}. {c['name']} [{c['lang']}] ({c['grade']}) - EUR {c['market_price']:.2f}"[:62], fill=(240, 242, 248), font=fm)
        y += 44
    bio = BytesIO(); bio.name = "portfolio.png"; img.save(bio, "PNG"); bio.seek(0)
    return bio

def make_csv(cards):
    s = StringIO()
    w = csv.writer(s, delimiter=";")
    w.writerow(["Nome", "Set", "Numero", "Lingua", "Grado", "Pagata EUR", "Valore EUR", "P&L EUR", "Data"])
    for c in cards:
        w.writerow([c["name"], c["set_name"], c["card_number"], c["lang"], c["grade"], f"{c['paid_price']:.2f}", f"{c['market_price']:.2f}", f"{c['market_price']-c['paid_price']:.2f}", c["added_at"]])
    bio = BytesIO(s.getvalue().encode("utf-8-sig"))
    bio.name = f"pokebinder_{datetime.now().strftime('%Y%m%d')}.csv"
    return bio
