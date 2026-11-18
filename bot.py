import os
import json
import sqlite3
import logging
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

# Configurazione log e chiavi
logging.basicConfig(level=logging.INFO)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
DB_FILE = "pokebinder.db"

FLAGS = {
    "ITA": "🇮🇹 ITA",
    "JAP": "🇯🇵 JAP",
    "ENG": "🇬🇧 ENG",
    "KOR": "🇰🇷 KOR",
}


# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            set_name TEXT,
            card_number TEXT,
            lang TEXT,
            grade TEXT,
            paid_price REAL,
            market_price REAL,
            photo_file_id TEXT,
            added_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_card_db(user_id, data, paid_price, photo_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO cards (user_id, name, set_name, card_number, lang, grade, paid_price, market_price, photo_file_id, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
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


# --- GRAFICA DASHBOARD PORTAFOGLIO ---
def generate_portfolio_card(cards):
    W, H = 900, 560
    img = Image.new("RGB", (W, H), color=(18, 20, 28))
    draw = ImageDraw.Draw(img)

    # Bordo dorato e riquadri interni
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

    # Intestazione
    draw.text((48, 45), "POKEBINDER PRO  |  PORTAFOGLIO", fill=(212, 175, 55), font=font_title)
    draw.text((710, 54), f"{len(cards)} Carte", fill=(170, 175, 190), font=font_sub)

    # Box 1: Valore Totale
    draw.text((70, 132), "VALORE ATTUALE", fill=(160, 168, 185), font=font_sub)
    draw.text((70, 170), f"EUR {total_val:,.2f}", fill=(255, 255, 255), font=font_big)
    draw.text((70, 230), f"Investito: EUR {total_paid:,.2f}", fill=(140, 148, 165), font=font_sub)

    # Box 2: Profitto / Perdita
    draw.text((490, 132), "RENDIMENTO (P&L)", fill=(160, 168, 185), font=font_sub)
    draw.text((490, 170), f"{sign}EUR {pnl:,.2f}", fill=pnl_color, font=font_big)
    draw.text((490, 230), f"{sign}{pnl_pct:.1f}% sul capitale", fill=pnl_color, font=font_sub)

    # Box 3: Top 3 Carte & Lingue
    draw.text((70, 322), "TOP CARTE IN COLLEZIONE", fill=(212, 175, 55), font=font_sub)
    top3 = cards[:3]
    y_pos = 360
    if not top3:
        draw.text((70, y_pos), "Nessuna carta ancora. Inviami una foto per iniziare!", fill=(180, 180, 190), font=font_med)
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


# --- MENU E TASTI ---
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Sfoglia Binder", callback_data="binder_0"),
            InlineKeyboardButton("📊 Portafoglio", callback_data="portfolio"),
        ],
        [
            InlineKeyboardButton("🔥 Radar Mercato", callback_data="market_radar"),
            InlineKeyboardButton("📸 Come aggiungere", callback_data="help_add"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    text = (
        "✨ *Benvenuto nel tuo PokeBinder Pro!*\n\n"
        "Gestisci la tua collezione *ITA, JAP, ENG e KOR* con un tocco:\n"
        "• 📸 *Mandami la foto di una carta* (anche in toploader o gradata) e la riconosco subito.\n"
        "• 📖 *Sfoglia il Binder* con foto, valori e guadagni.\n"
        "• 📊 *Statistiche Portafoglio* con card grafica.\n"
        "• 🔥 *Radar Mercato* con le carte più calde e rivalutate."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


# --- ANALISI FOTO CON GEMINI ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait_msg = await update.message.reply_text("🔍 *Analizzo la carta e cerco le quotazioni...*", parse_mode="Markdown")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()

    prompt = """
    Analizza questa immagine di una carta Pokémon (può essere Raw, in toploader o gradata PSA/CGC/BGS/GRAAD).
    Identifica con precisione:
    1. Nome della carta (es. "Umbreon VMAX Alt Art" o "Magikarp AR")
    2. Nome del set e codice (es. "Evolving Skies" o "Triplet Beat")
    3. Numero carta (es. "215/203" o "080/073")
    4. Lingua esatta scegliendo SOLO tra: ITA, JAP, ENG, KOR
    5. Condizione/Grado (es. "Raw" oppure "PSA 10", "BGS 9.5", "GRAAD 9", ecc.)
    6. Stima realistica del prezzo attuale di mercato in EUR (numero decimale, es. 45.50) basata su Cardmarket/eBay per quella specifica lingua e grado.

    Rispondi SOLO con un JSON valido in questo formato esatto, senza markdown:
    {"name": "...", "set_name": "...", "card_number": "...", "lang": "JAP", "grade": "Raw", "market_price": 45.50}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=bytes(img_bytes), mime_type="image/jpeg"),
                prompt,
            ],
        )
        raw_json = response.text.strip().replace("```json", "").replace("```", "").strip()
        card_data = json.loads(raw_json)
        context.user_data["pending_card"] = card_data
        context.user_data["pending_photo"] = photo.file_id
        context.user_data["awaiting_price"] = True

        flag = FLAGS.get(card_data.get("lang", "ENG").upper(), "🌐")
        msg = (
            f"✨ *Carta riconosciuta!*\n\n"
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
        await wait_msg.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Non sono riuscito a leggere bene la carta, riprova con una foto più nitida.\n({e})")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_price"):
        txt = update.message.text.replace(",", ".").replace("€", "").strip()
        try:
            paid = float(txt)
            card_data = context.user_data.pop("pending_card")
            photo_id = context.user_data.pop("pending_photo")
            context.user_data["awaiting_price"] = False
            add_card_db(update.effective_user.id, card_data, paid, photo_id)
            await update.message.reply_text(
                f"🎉 *{card_data['name']}* aggiunta al tuo Binder!\nPagata: *{paid:.2f} €* | Valore: *{float(card_data['market_price']):.2f} €*",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )
        except ValueError:
            await update.message.reply_text("✍️ Scrivi un numero valido per il prezzo pagato (es. `30` oppure `12.50`).", parse_mode="Markdown")
    else:
        await update.message.reply_text("📸 Mandami una foto di una carta per aggiungerla, oppure usa il menu:", reply_markup=main_menu_keyboard())


# --- NAVIGAZIONE E PULSANTI ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "menu":
        await query.message.reply_text("🏠 *Menu Principale PokeBinder*", parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "help_add":
        await query.message.reply_text(
            "📸 *Aggiunta Rapida con Foto*\n\n"
            "Scatta o carica una foto della carta (anche dentro raccoglitore, toploader o slab gradata).\n"
            "Riconosco in automatico nome, set, lingua (*ITA, JAP, ENG, KOR*), grado e quotazione!",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "save_free":
        if "pending_card" in context.user_data:
            card_data = context.user_data.pop("pending_card")
            photo_id = context.user_data.pop("pending_photo")
            context.user_data["awaiting_price"] = False
            add_card_db(user_id, card_data, 0.0, photo_id)
            await query.edit_message_text(
                f"🎉 *{card_data['name']}* salvata nel Binder (pagata 0 €)!",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )

    elif data == "cancel_add":
        context.user_data.clear()
        await query.edit_message_text("❌ Aggiunta annullata.", reply_markup=main_menu_keyboard())

    elif data == "portfolio":
        cards = get_user_cards(user_id)
        img_bio = generate_portfolio_card(cards)
        await query.message.reply_photo(
            photo=img_bio,
            caption="📊 *Ecco la panoramica aggiornata del tuo portafoglio!*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data.startswith("binder_"):
        idx = int(data.split("_")[1])
        cards = get_user_cards(user_id)
        if not cards:
            await query.message.reply_text("📭 Il tuo binder è ancora vuoto! Mandami una foto di una carta per iniziare.", reply_markup=main_menu_keyboard())
            return
        idx = max(0, min(idx, len(cards) - 1))
        c = cards[idx]
        diff = c["market_price"] - c["paid_price"]
        arrow = "📈 +" if diff >= 0 else "📉 "
        flag = FLAGS.get(c["lang"], c["lang"])

        caption = (
            f"📖 *BINDER — Carta {idx + 1} di {len(cards)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🃏 *{c['name']}* (`{c['card_number']}`)\n"
            f"📦 Set: _{c['set_name']}_\n"
            f"🌍 Lingua: *{flag}*  |  🏷 Stato: *{c['grade']}*\n\n"
            f"💵 Pagata: *{c['paid_price']:.2f} €*\n"
            f"💎 Valore attuale: *{c['market_price']:.2f} €*\n"
            f"{arrow}*{diff:.2f} €*"
        )
        nav = []
        if idx > 0:
            nav.append(InlineKeyboardButton("⬅️ Prec.", callback_data=f"binder_{idx - 1}"))
        nav.append(InlineKeyboardButton(f"{idx + 1}/{len(cards)}", callback_data="noop"))
        if idx < len(cards) - 1:
            nav.append(InlineKeyboardButton("Succ. ➡️", callback_data=f"binder_{idx + 1}"))

        kb = InlineKeyboardMarkup([
            nav,
            [
                InlineKeyboardButton("🗑 Elimina", callback_data=f"del_{c['id']}"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu"),
            ],
        ])
        await query.message.reply_photo(photo=c["photo_file_id"], caption=caption, parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("del_"):
        card_id = int(data.split("_")[1])
        delete_card_db(card_id, user_id)
        await query.edit_message_caption(caption="🗑 *Carta rimossa dal binder.*", parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "market_radar":
        wait = await query.message.reply_text("📡 *Scansiono i trend di mercato ITA/JAP/ENG/KOR...*", parse_mode="Markdown")
        radar_prompt = """
        Fornisci un report sintetico e curato in italiano sul mercato Pokémon TCG attuale (Cardmarket e mercato Giapponese/Internazionale).
        Dividi in due brevi classifiche da 4 carte ciascuna:
        1. 🔥 CARTE PIÙ CERCATE DEL MOMENTO (alta liquidità e domanda)
        2. 📈 CARTE CON MAGGIOR RIVALUTAZIONE RECENTE (con percentuale stimata e fascia di prezzo in EUR)
        Usa emoji e formattazione pulita per Telegram (grassetto con *singolo asterisco*). Massimo 1200 caratteri.
        """
        res = client.models.generate_content(model="gemini-2.5-flash", contents=radar_prompt)
        await wait.edit_text(res.text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PokeBinder is running!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

def main():
    init_db()
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()


if __name__ == "__main__":
    main()
