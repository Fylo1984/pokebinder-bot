import os, logging, asyncio, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from extra import *

logging.basicConfig(level=logging.INFO)

def menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Sfoglia Binder", callback_data="binder_all_0"), InlineKeyboardButton("📊 Portafoglio", callback_data="portfolio")],
        [InlineKeyboardButton("🔥 Radar Mercato", callback_data="radar"), InlineKeyboardButton("🎯 Wishlist", callback_data="wish_view")],
        [InlineKeyboardButton("🔄 Aggiorna Prezzi", callback_data="sync_prices"), InlineKeyboardButton("📥 Esporta CSV", callback_data="export_csv")],
        [InlineKeyboardButton("💾 Backup DB", callback_data="backup_db"), InlineKeyboardButton("➕ Guida", callback_data="help")]
    ])

async def start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    init_db()
    await u.message.reply_text("✨ *PokeBinder Pro attivo!*\n\n• 📸 Manda una *foto* o scrivi *nome e numero*\n• 💾 Per ripristinare un backup, inviami il file `.db` qui in chat", parse_mode="Markdown", reply_markup=menu_kb())

async def confirm_card(msg, ctx, d, pref):
    ctx.user_data.update({"card": d, "photo": pref, "wait_price": True, "wait_wish": False})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Salva (0 €)", callback_data="save0"), InlineKeyboardButton("❌ Annulla", callback_data="cancel")]])
    await msg.edit_text(f"✨ *{d['name']}* (`{d['card_number']}`)\n📦 _{d['set_name']}_\n🌍 *{FLAGS.get(d['lang'].upper(), d['lang'])}* | 🏷 *{d['grade']}*\n💶 Valore Cardmarket/Live: *{float(d['market_price']):.2f} €*\n\n💰 *Quanto l'hai pagata in Euro?* (scrivi solo il numero)", parse_mode="Markdown", reply_markup=kb)

async def on_doc(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = u.message.document
    if not doc or not doc.file_name.endswith(".db"): return
    f = await ctx.bot.get_file(doc.file_id)
    await f.download_to_drive(DB)
    init_db()
    await u.message.reply_text("✅ *Backup ripristinato!* Tutte le tue carte e la Wishlist sono tornate online.", parse_mode="Markdown", reply_markup=menu_kb())

async def on_photo(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    w = await u.message.reply_text("🔍 *Analizzo la carta e cerco il prezzo...*", parse_mode="Markdown")
    f = await ctx.bot.get_file(u.message.photo[-1].file_id)
    b = await f.download_as_bytearray()
    p = 'Identifica questa carta Pokemon (Raw o gradata) e stima il prezzo attuale in EUR. Rispondi SOLO con JSON: {"name":"...","set_name":"...","card_number":"...","lang":"ITA/JAP/ENG/KOR","grade":"Raw","market_price":45.50}'
    try:
        txt = await asyncio.wait_for(asyncio.to_thread(ask_gemini, [types.Part.from_bytes(data=bytes(b), mime_type="image/jpeg"), p], True), timeout=30)
        d = parse_json(txt)
        if d.get("lang","").upper() not in ("JAP","KOR") and d.get("grade","Raw").lower() == "raw":
            cm = await asyncio.to_thread(cm_search, d["name"], d["card_number"])
            if cm: d["market_price"] = cm["price"]
        await confirm_card(w, ctx, d, u.message.photo[-1].file_id)
    except Exception as e: await w.edit_text(f"⚠️ Riprova tra qualche secondo. ({e})")

async def on_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = u.message.text.strip()
    if ctx.user_data.get("wait_wish"):
        try:
            parts = t.rsplit(",", 1)
            name, target = parts[0].strip(), float(parts[1].replace("€","").strip())
            add_wish(u.effective_user.id, name, target)
            ctx.user_data["wait_wish"] = False
            await u.message.reply_text(f"🎯 *{name}* aggiunta in Wishlist (Target: *{target:.2f} €*)!", parse_mode="Markdown", reply_markup=menu_kb())
        except Exception:
            await u.message.reply_text("✍️ Scrivi nel formato `Nome Numero, Prezzo` (es. `Magikarp 080/073 JAP, 45`).", parse_mode="Markdown")
        return
    if ctx.user_data.get("wait_price"):
        try:
            paid = float(t.replace(",",".").replace("€","").strip())
            d, pid = ctx.user_data.pop("card"), ctx.user_data.pop("photo"); ctx.user_data["wait_price"] = False
            add_card(u.effective_user.id, d, paid, pid)
            await u.message.reply_text(f"🎉 *{d['name']}* salvata nel Binder!", parse_mode="Markdown", reply_markup=menu_kb())
        except ValueError: await u.message.reply_text("✍️ Scrivi solo il numero in Euro (es. `25`), oppure premi Annulla.")
        return
    w = await u.message.reply_text(f"🔎 *Cerco* `{t}` *su Cardmarket...*", parse_mode="Markdown")
    p = f'Identifica la carta Pokemon "{t}" e stima il prezzo attuale in EUR. Rispondi SOLO con JSON: {{"name":"...","set_name":"...","card_number":"...","lang":"ITA/JAP/ENG/KOR","grade":"Raw","market_price":45.50}}'
    try:
        txt = await asyncio.wait_for(asyncio.to_thread(ask_gemini, p, True), timeout=30)
        d = parse_json(txt)
        cm = await asyncio.to_thread(cm_search, d["name"], d["card_number"])
        if cm and d.get("lang","").upper() not in ("JAP","KOR") and d.get("grade","Raw").lower() == "raw":
            d["market_price"] = cm["price"]
        img = (cm["img"] if cm and cm["img"] else await asyncio.to_thread(fetch_img, d["name"], d["card_number"]))
        await confirm_card(w, ctx, d, img)
    except Exception as e: await w.edit_text(f"⚠️ Riprova tra qualche secondo ({e}). Es: `Magikarp 080/073 JAP`.")

async def on_btn(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer(); uid, data = q.from_user.id, q.data
    if data == "menu": ctx.user_data.clear(); await q.message.reply_text("🏠 *Menu PokeBinder*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "help": await q.message.reply_text("📸 Manda una foto o scrivi nome e numero.\n💾 Salva il file `.db` da *Backup DB* e reinvialo in chat dopo ogni riavvio di Render.", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "save0" and "card" in ctx.user_data:
        d, pid = ctx.user_data.pop("card"), ctx.user_data.pop("photo"); ctx.user_data["wait_price"] = False
        add_card(uid, d, 0.0, pid)
        await q.edit_message_text(f"🎉 *{d['name']}* salvata (0 €)!", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "cancel": ctx.user_data.clear(); await q.edit_message_text("❌ Annullato.", reply_markup=menu_kb())
    elif data == "portfolio": await q.message.reply_photo(photo=make_portfolio(get_cards(uid)), caption="📊 *Portafoglio aggiornato*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "export_csv":
        cards = get_cards(uid)
        if not cards: return await q.message.reply_text("📭 Binder vuoto!", reply_markup=menu_kb())
        await q.message.reply_document(document=make_csv(cards), caption="📥 *Esportazione Excel/CSV pronta!*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "backup_db":
        if not os.path.exists(DB): return await q.message.reply_text("📭 Nessun database da salvare.", reply_markup=menu_kb())
        with open(DB, "rb") as f: await q.message.reply_document(document=f, filename="pokebinder.db", caption="💾 *Backup salvato!* Per ripristinarlo in futuro, inoltrami questo file qui in chat.", parse_mode="Markdown")
    elif data == "sync_prices":
        cards = get_cards(uid)
        if not cards: return await q.message.reply_text("📭 Binder vuoto!", reply_markup=menu_kb())
        w = await q.message.reply_text(f"🔄 *Aggiorno le quotazioni di {len(cards)} carte...*", parse_mode="Markdown")
        lst = "\n".join([f'{c["id"]}: {c["name"]} {c["card_number"]} {c["lang"]} ({c["grade"]})' for c in cards[:25]])
        p = f'Stima il prezzo attuale in EUR su Cardmarket/PriceCharting per queste carte Pokemon:\n{lst}\nRispondi SOLO con JSON ID:prezzo, es: {{"1": 45.5, "2": 120.0}}'
        try:
            res = parse_json(await asyncio.wait_for(asyncio.to_thread(ask_gemini, p, True), timeout=35))
            for k, v in res.items(): update_price(int(k), uid, v)
            await w.edit_text("✅ *Quotazioni aggiornate!* Apri il Portafoglio per vedere il nuovo P&L.", parse_mode="Markdown", reply_markup=menu_kb())
        except Exception as e: await w.edit_text(f"⚠️ Errore aggiornamento: {e}", reply_markup=menu_kb())
    elif data.startswith("binder_"):
        _, flt, idx_s = data.split("_"); cards = get_cards(uid, flt)
        f_kb = [InlineKeyboardButton(l, callback_data=f"binder_{k}_0") for l, k in [("Tutte","all"),("🇯🇵","JAP"),("🇮🇹","ITA"),("🏆","graded"),("📈 P&L","pnl")]]
        if not cards: return await q.message.reply_text("📭 Nessuna carta con questo filtro.", reply_markup=InlineKeyboardMarkup([f_kb, [InlineKeyboardButton("🏠 Menu", callback_data="menu")]]))
        idx = max(0, min(int(idx_s), len(cards)-1)); c = cards[idx]; diff = c["market_price"] - c["paid_price"]
        nav = ([InlineKeyboardButton("⬅️", callback_data=f"binder_{flt}_{idx-1}")] if idx>0 else []) + [InlineKeyboardButton(f"{idx+1}/{len(cards)}", callback_data="x")] + ([InlineKeyboardButton("➡️", callback_data=f"binder_{flt}_{idx+1}")] if idx<len(cards)-1 else [])
        kb = InlineKeyboardMarkup([f_kb, nav, [InlineKeyboardButton("🗑 Elimina", callback_data=f"del_{c['id']}"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]])
        await q.message.reply_photo(photo=c["photo_file_id"], caption=f"📖 *Carta {idx+1}/{len(cards)}* (Filtro: `{flt}`)\n🃏 *{c['name']}* (`{c['card_number']}`)\n📦 _{c['set_name']}_ | *{FLAGS.get(c['lang'],c['lang'])}* | *{c['grade']}*\n💵 Pagata: *{c['paid_price']:.2f} €* | 💎 Valore: *{c['market_price']:.2f} €* ({'+' if diff>=0 else ''}{diff:.2f} €)", parse_mode="Markdown", reply_markup=kb)
    elif data.startswith("del_"): del_card(int(data.split("_")[1]), uid); await q.edit_message_caption("🗑 *Carta eliminata.*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "wish_view":
        ws = get_wish(uid)
        txt = "🎯 *LA TUA WISHLIST*\n\n" + ("\n".join([f"• *{w['name']}* — Target: *{w['target_price']:.2f} €*" for w in ws]) if ws else "Nessuna carta in Wishlist.")
        rows = [[InlineKeyboardButton(f"❌ {w['name'][:22]}", callback_data=f"wdel_{w['id']}")] for w in ws[:5]]
        rows.append([InlineKeyboardButton("➕ Aggiungi", callback_data="wish_add"), InlineKeyboardButton("🔍 Controlla Prezzi", callback_data="wish_check")])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu")])
        await q.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    elif data == "wish_add":
        ctx.user_data["wait_wish"] = True
        await q.message.reply_text("🎯 Scrivimi la carta e il tuo prezzo target separati da una virgola:\nEs: `Giratina V 186/196 ENG, 320`", parse_mode="Markdown")
    elif data.startswith("wdel_"): del_wish(int(data.split("_")[1]), uid); await q.edit_message_text("🗑 *Rimossa dalla Wishlist.*", parse_mode="Markdown", reply_markup=menu_kb())
    elif data == "wish_check":
        ws = get_wish(uid)
        if not ws: return await q.message.reply_text("🎯 Wishlist vuota!", reply_markup=menu_kb())
        w = await q.message.reply_text("🔍 *Controllo i prezzi live della Wishlist...*", parse_mode="Markdown")
        lst = "\n".join([f"- {x['name']} (Target: {x['target_price']} EUR)" for x in ws])
        try:
            rep = await asyncio.wait_for(asyncio.to_thread(ask_gemini, f"Confronta il prezzo attuale in EUR su Cardmarket/PriceCharting di queste carte Pokemon col Target. Rispondi in italiano breve con emoji (🟢 vicino/sotto target, 🔴 alto), senza asterischi:\n{lst}", True), timeout=35)
            await w.edit_text(f"🎯 *CONTROLLO WISHLIST*\n\n{rep}", parse_mode="Markdown", reply_markup=menu_kb())
        except Exception as e: await w.edit_text(f"⚠️ Errore: {e}", reply_markup=menu_kb())
    elif data == "radar":
        w = await q.message.reply_text("📡 *Analizzo i dati reali Cardmarket...*", parse_mode="Markdown")
        try:
            items = await asyncio.wait_for(asyncio.to_thread(radar_live), timeout=30)
            media = [InputMediaPhoto(media=i["img"], caption=f"{i['tag']} {i['name']} ({i['num']}) — {i['price']:.2f} € ({i['delta']:+.1f}%)") for i in items if i["img"]]
            try: await q.message.reply_media_group(media=media)
            except Exception: pass
            rep = "🔥 *RADAR CARDMARKET & PREVISIONI*\n\n" + "\n\n".join([f"{i['tag']}\n🃏 *{i['name']}* (`{i['num']}`) — _{i['set']}_\n💶 *{i['price']:.2f} €* (vs 30gg: *{i['delta']:+.1f}%*)\n💡 {i['reason']}" for i in items])
            await w.edit_text(rep, parse_mode="Markdown", reply_markup=menu_kb())
        except Exception as e: await w.edit_text(f"⚠️ Errore Radar: {e}", reply_markup=menu_kb())

class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever(), daemon=True).start()
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_btn))
    app.run_polling()

if __name__ == "__main__": main()
