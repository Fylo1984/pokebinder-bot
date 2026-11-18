import os
import json
import sqlite3
import logging
import asyncio
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
DB_FILE = "pokebinder.db"
MODEL_NAME = "gemini-3.8-flash"

FLAGS = {
    "ITA": "🇮🇹 ITA",
    "JAP": "🇯🇵 JAP",
    "ENG": "🇬🇧 ENG",
    "KOR": "🇰🇷 KOR",
}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS cards ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, "
        "set_name TEXT, card_number TEXT, lang TEXT, grade TEXT, "
        "paid_price REAL, market_price REAL, photo_file_id TEXT, added_at TEXT)"
    )
    conn.commit()
    conn.close()


def add_card_db(user_id, data, paid_price, photo_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO cards (user_id, name, set_name, card_number, lang, grade, paid_price, market_price, photo_file_id, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            data.get("name", "Sconosciuta"),
            data.get("set_name", "N/D"),
            data.get("card_number", ""),
            data.get("lang", "ENG").upper(),
            data.get("grade", "Raw"),
            paid_price,
            float(data.get("market_price", 0)),
            photo_id,
            datetime.now().strftime("%Y-%m-%d"),
        ),
    )
    conn.commit()
    conn.close()


def get_user_cards(user_id):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM cards WHERE user_id = ? ORDER BY market_price DESC", (user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_card_db(card_id, user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, user_id))
    conn.commit()
    conn.close()


def fetch_official_image_url(name, card_number):
    try:
        clean_name = name.split()[0]
        r = requests.get(f"https://api.tcgdex.net/v2/en/cards?name={clean_name}", timeout=5)
        if r.status_code == 200:
            items = r.json()
            num_part = card_number.split("/")[0].lstrip("0")
            for item in items:
                if item.get("image") and (not num_part or item.get("localId", "").lstrip("0") == num_part):
                    return f"{item['image']}/high.webp"
            for item in items:
                if item.get("image"):
                    return f"{item['image']}/high.webp"
    except Exception:
        pass
    return "https://images.pokemontcg.io/base1/4_hires.png"


def generate_portfolio_card(cards):
    W, H = 900, 560
    img = Image.new("RGB", (W, H), color=(18, 20, 28))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=24, outline=(212, 175, 55), width=3)
    draw.rounded_rectangle([45, 110, 435, 280], radius=16, fill=(28, 32, 44))
    draw.rounded_rectangle([465, 110, 855, 280], radius=16, fill=(28, 32, 44))
    draw.rounded_rectangle([45, 305, 855, 515], radius=16, fill=(28, 32, 44))

    font_title = ImageFont.load_default(size=30)
    font_sub = ImageFont.load_default(size=18)
    font_big = ImageFont.load_default(size=38)
    font_med = ImageFont.load_default(size=22)

    total_val = sum(c["market_price"] for c in cards)
    total_paid = sum(c["paid_price"] for c in cards)
    pnl = total_val - total_paid
    pnl_pct = ((pnl / total_paid) * 100) if total_paid > 0 else 0.0
    pnl_color = (72, 219, 132) if pnl >= 0 else (255, 95, 95)
    sign = "+" if pnl >= 0 else ""

    draw.text((48, 45), "POKEBINDER PRO  |  PORTAFOGLIO", fill=(212, 175, 55), font=font_title)
    draw.text((710, 54), f"{len(cards)} Carte", fill=(170, 175, 190), font=font_sub)

    draw.text((70, 132), "VALORE ATTUALE", fill=(160, 168, 185), font=font_sub)
    draw.text((70, 170), f"EUR {total_val:,.2f}", fill=(255, 255, 255), font=font_big)
    draw.text((70, 230), f"Investito: EUR {total_paid:,.2f}", fill=(140, 148, 165), font=font_sub)

    draw.text((490, 132), "RENDIMENTO (P&L)", fill=(160, 168, 185), font=font_sub)
    draw.text((490, 170), f"{sign}EUR {pnl:,.2f}", fill=pnl_color, font=font_big)
    draw.text((490, 230), f"{sign}{pnl_pct:.1f}% sul capitale", fill=pnl_color, font=font_sub)

    draw.text((70, 322), "TOP CARTE IN COLLEZIONE", fill=(212, 175, 55), font=font_sub)
    top3 = cards[:3]
    y_pos = 360
    if not top3:
        draw.text((70, y_pos), "Nessuna carta ancora. Inviami una foto o il numero!", fill=(180, 180, 190), font=font_med)
    else:
        for i, c in enumerate(top3, 1):
            label = f"{i}. {c['name']} [{c['lang']}] ({c['grade']})  —  EUR {c['market_price']:.2f}"
            draw.text((70, y_pos), label[:62], fill=(240, 242, 248), font=font_med)
            y_pos += 44

    bio = BytesIO()
    bio.name = "portfolio.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Sfoglia Binder", callback_data="binder_0"),
            InlineKeyboardButton("📊 Portafoglio", callback_data="portfolio"),
        ],
        [
            InlineKeyboardButton("🔥 Radar Mercato", callback_data="market_radar"),
            InlineKeyboardButton("➕ Come aggiungere", callback_data="help_add"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    text = (
        "✨ *Benvenuto nel tuo PokeBinder Pro!*\n\n"
        "Aggiungi carte *ITA, JAP, ENG e KOR* in due modi:\n"
        "• 📸 *Mandami una foto* della carta (raw, toploader o gradata).\n"
        "• ✍️ *Scrivimi nome e numero* (es. `Magikarp 080/073 JAP` oppure `Charizard 199/165 ITA PSA 10`).\n"
        "• 📖 *Sfoglia il Binder* e controlla il *Portafoglio*!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def send_card_confirmation(target_msg, context, card_data, photo_ref):
    context.user_data["pending_card"] = card_data
    context.user_data["pending_photo"] = photo_ref
    context.user_data["awaiting_price"] = True

    flag = FLAGS.get(card_data.get("lang", "ENG").upper(), "🌐")
    msg = (
        f"✨ *Carta identificata!*\n\n"
        f"🃏 *{card_data['name']}* (`{card_data['card_number']}`)\n"
        f"📦 Set: _{card_data['set_name']}_\n"
        f"🌍 Lingua: *{flag}*  |  🏷 Stato: *{card_data['grade']}*\n"
        f"💶 Valore stimato: *{float(card_data['market_price']):.2f} €*\n\n"
        f"💰 *Quanto l'hai pagata in Euro?*\n"
        f"_(Scrivimi solo il numero in chat, es. `25`, oppure tocca il tasto qui sotto)_"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Salva (Pagata 0 €)", callback_data="save_free"),
            InlineKeyboardButton("❌ Annulla", callback_data="cancel_add"),
        ]
    ])
    await target_msg.edit_text(msg, parse_mode="Markdown", reply_markup=kb)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait_msg = await update.message.reply_text("🔍 *Analizzo la foto e cerco le quotazioni...*", parse_mode="Markdown")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()

    prompt = (
        "Analizza questa immagine di una carta Pokemon (Raw, in toploader o gradata PSA/CGC/BGS/GRAAD). "
        "Identifica: 1) Nome della carta, 2) Nome del set, 3) Numero carta (es. 215/203), "
        "4) Lingua scegliendo SOLO tra ITA, JAP, ENG, KOR, 5) Grado (es. Raw, PSA 10, GRAAD 9), "
        "6) Stima realistica del prezzo attuale in EUR (numero decimale). "
        'Rispondi SOLO con JSON valido senza markdown: {"name": "...", "set_name": "...", "card_number": "...", "lang": "JAP", "grade": "Raw", "market_price": 45.50}'
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_bytes(data=bytes(img_bytes), mime_type="image/jpeg"),
                prompt,
            ],
        )
        raw_json = response.text.strip().replace("```json", "").replace("```", "").strip()
        card_data = json.loads(raw_json)
        await send_card_confirmation(wait_msg, context, card_data, photo.file_id)
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Errore nella lettura: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()

    if context.user_data.get("awaiting_price"):
        clean_num = txt.replace(",", ".").replace("€", "").strip()
        try:
            paid = float(clean_num)
            card_data = context
