import os, json, sqlite3, logging, asyncio, threading, requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DB = "pokebinder.db"
MODEL = "gemini-3.8-flash"
FLAGS = {"ITA": "🇮🇹 ITA", "JAP": "🇯🇵 JAP", "ENG": "🇬🇧 ENG", "KOR": "🇰🇷 KOR"}

def init_db():
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, set_name TEXT, card_number TEXT, lang TEXT, grade TEXT, paid_price REAL, market_price REAL, photo_file_id TEXT, added_at TEXT)")

def add_card(uid, d, paid, pid):
    with sqlite3.connect(DB) as c:
        c.execute("INSERT INTO cards (user_id,name,set_name,card_number,lang,grade,paid_price,market_price,photo_file_id,added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (uid, d.get("name","?"), d.get("set_name","N/D"), d.get("card_number",""), d.get("lang","ENG").upper(), d.get("grade","Raw"), paid, float(d.get("market_price",0)), pid, datetime.now().strftime("%Y-%m-%d")))

def get_cards(uid):
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM cards WHERE user_id=? ORDER BY market_price DESC", (uid,)).fetchall()]

def del_card(cid, uid):
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM cards WHERE id=? AND user_id=?", (cid, uid))

def fetch_img(name, num):
    try:
        r = requests.get(f"https://api.tcgdex.net/v2/en/cards?name={name.split()[0]}", timeout=5)
        n = num.split("/")[0].lstrip("0")
        for i in r.json():
            if i.get("image") and (not n or i.get("localId","").lstrip("0") == n): return f"{i['image']}/high.webp"
        for i in r.json():
            if i.get("image"): return f"{i['image']}/high.webp"
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

def menu_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📖 Sfoglia Binder", callback_data="binder_0"), InlineKeyboardButton("📊 Portafoglio", callback_data="portfolio")], [InlineKeyboardButton("🔥 Radar Mercato", callback_data="radar"), InlineKeyboardButton("➕ Come aggiungere", callback_data="help")]])

async def start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    init_db()
    await u.message.reply_text("✨ *PokeBinder Pro attivo!*\n\n• 📸 Mandami una *foto* della carta\n• ✍️ Oppure scrivimi *nome e numero* (es. `Magikarp 080/073 JAP`)", parse_mode="Markdown", reply_markup=menu_kb())

async def confirm_card(msg, ctx, d, pref):
    ctx.user_data.update({"card": d, "photo": pref, "wait_price": True})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Salva (0 €)", callback_data="save0"), InlineKeyboardButton("❌ Annulla", callback_data="cancel")]])
    await msg.edit_text(f"✨ *{d['name']}* (`{d['card_number']}`)\n📦 _{d['set_name']}_\n🌍 *{FLAGS.get(d['lang'].upper(), d['lang'])}* | 🏷 *{d['grade']}*\n💶 Valore stimato: *{float(d['market_price']):.2f} €*\n\n💰 *Quanto l'hai pagata in Euro?* (scrivi solo il numero)", parse_mode="Markdown", reply_markup=kb)

async def on_photo(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    w = await u.message.reply_text("🔍 *Analizzo la carta...*", parse_mode="Markdown")
    f = await ctx.bot.get_file(u.message.photo[-1].file_id)
    b = await f.download_as_bytearray()
    p = 'Identifica questa carta Pokemon (Raw o gradata). Rispondi SOLO con JSON senza markdown: {"name":"...","set_name":"...","card_number":"...","lang":"ITA/JAP/ENG/KOR","grade":"Raw o PSA 10 ecc","market_price":45.50}'
    try:
        r = client.models.generate_content(model=MODEL, contents=[types.Part.from_bytes(data=bytes(b), mime_type="image/jpeg"), p])
        d = json.loads(r.text.strip().replace("```json","").replace("```","").strip())
        await confirm_card(w, ctx, d, u.message.photo[-1].file_id)
    except Exception as e:
        await w.edit_text(f"⚠️ Errore: {e}")

async def on_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = u.message.text.strip()
    if ctx.user_data.get("wait_price"):
        try:
            paid = float(t.replace(",",".").replace("€","").strip())
            d, pid = ctx.user_data.pop("card"), ctx.user_data.pop("photo")
            ctx.user_data["wait_price"] = False
            add_card(u.effective_user.id, d, paid, pid)
            await u.message.reply_text(f"🎉 *{d['name']}* salvata nel Binder!", parse_mode="Markdown", reply_markup=menu_kb())
        except ValueError:
            await u.message.reply_text("✍️ Scrivi solo il numero in Euro (es. `25`), oppure premi Annulla.")
        return
    w = await u.message.reply_text(f"🔎 *Cerco* `{t}`...", parse_mode="Markdown")
    p = f'Identifica la carta Pokemon "{t}". Rispondi SOLO con JSON senza markdown: {{"name":"...","set_name":"...","card_number":"...","lang":"ITA/JAP/ENG/KOR","grade":"Raw","market_price":45.50}}'
    try:
        r = client.models.generate_content(model=MODEL, contents=p)
        d = json.loads(r.text.strip().replace("```json","").replace("```","").strip())
        await confirm_card(w, ctx, d, fetch_img(d["name"], d["card_number"]))
    except Exception as e:
        await w.edit_text(f"⚠️ Non trovata ({e}). Riprova con nome e numero (es. `Magikarp 080/073 JAP`).")

async def on_btn(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer(); uid, data = q.from_user.id, q.data
    if data == "menu": await q.message.reply_text("🏠 *Menu PokeBinder*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "help": await q.message.reply_text("📸 Manda una foto oppure scrivi nome e numero (es. `Umbreon VMAX 215/203 ENG`).", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "save0" and "card" in ctx.user_data:
        d, pid = ctx.user_data.pop("card"), ctx.user_data.pop("photo"); ctx.user_data["wait_price"] = False
        add_card(uid, d, 0.0, pid)
        await q.edit_message_text(f"🎉 *{d['name']}* salvata (0 €)!", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "cancel": ctx.user_data.clear(); await q.edit_message_text("❌ Annullato.", reply_markup=menu_kb())
    elif data == "portfolio": await q.message.reply_photo(photo=make_portfolio(get_cards(uid)), caption="📊 *Portafoglio aggiornato*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data.startswith("binder_"):
        cards = get_cards(uid)
        if not cards: return await q.message.reply_text("📭 Binder vuoto! Manda una foto o scrivi il numero carta.", reply_markup=menu_kb())
        idx = max(0, min(int(data.split("_")[1]), len(cards)-1)); c = cards[idx]; diff = c["market_price"] - c["paid_price"]
        nav = ([InlineKeyboardButton("⬅️", callback_data=f"binder_{idx-1}")] if idx>0 else []) + [InlineKeyboardButton(f"{idx+1}/{len(cards)}", callback_data="x")] + ([InlineKeyboardButton("➡️", callback_data=f"binder_{idx+1}")] if idx<len(cards)-1 else [])
        kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("🗑 Elimina", callback_data=f"del_{c['id']}"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]])
        await q.message.reply_photo(photo=c["photo_file_id"], caption=f"📖 *Carta {idx+1}/{len(cards)}*\n🃏 *{c['name']}* (`{c['card_number']}`)\n📦 _{c['set_name']}_ | *{FLAGS.get(c['lang'],c['lang'])}* | *{c['grade']}*\n💵 Pagata: *{c['paid_price']:.2f} €* | 💎 Valore: *{c['market_price']:.2f} €* ({'+' if diff>=0 else ''}{diff:.2f} €)", parse_mode="Markdown", reply_markup=kb)
    elif data.startswith("del_"): del_card(int(data.split("_")[1]), uid); await q.edit_message_caption("🗑 *Carta eliminata.*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "radar":
        w = await q.message.reply_text("📡 *Scansiono il mercato...*", parse_mode="Markdown")
        try:
            r = client.models.generate_content(model=MODEL, contents="Report breve in italiano sul mercato Pokemon TCG attuale: 4 carte piu cercate e 4 con maggior rivalutazione in EUR. Solo testo ed emoji, senza asterischi.")
            await w.edit_text(r.text, reply_markup=menu_kb())
        except Exception as e: await w.edit_text(f"⚠️ Errore Radar: {e}", reply_markup=menu_kb())

