import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)
from telethon import TelegramClient
from telethon.errors import PhoneCodeInvalidError, PhoneNumberInvalidError, SessionPasswordNeededError
from telethon.tl.types import Channel, Chat

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.sqlite3"
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_INTERVAL = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("announcement-bot")

PHONE, CODE, PASSWORD, ANNOUNCEMENT, INTERVAL, GROUP_LINK = range(6)
clients: dict[int, TelegramClient] = {}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, phone TEXT NOT NULL, session_name TEXT NOT NULL UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, account_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, title TEXT NOT NULL, username TEXT, invite_link TEXT, UNIQUE(user_id, chat_id));
        CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, announcement TEXT, interval_minutes INTEGER NOT NULL DEFAULT 60);
        """)
        c.execute("INSERT OR IGNORE INTO users(telegram_id, status) VALUES (?, 'approved')", (ADMIN_ID,))


def approved(uid: int) -> bool:
    if uid == ADMIN_ID:
        return True
    with db() as c:
        row = c.execute("SELECT status FROM users WHERE telegram_id=?", (uid,)).fetchone()
        return bool(row and row["status"] == "approved")


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Telegram hesabı ekle", callback_data="account_add"), InlineKeyboardButton("📋 Grupları getir", callback_data="groups_import")],
        [InlineKeyboardButton("🔗 Link ile grup ekle", callback_data="group_link"), InlineKeyboardButton("🗑 Grupları sil", callback_data="groups_delete")],
        [InlineKeyboardButton("📣 Duyuru mesajı", callback_data="announcement"), InlineKeyboardButton("⏱ Sıklık ayarla", callback_data="interval")],
        [InlineKeyboardButton("👥 Hesaplarım", callback_data="accounts"), InlineKeyboardButton("ℹ️ Durum", callback_data="status")],
        [InlineKeyboardButton("⚙️ Kullanıcı yönetimi", callback_data="users")],
    ])


def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Ana menü", callback_data="home")]])


async def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    u = update.effective_user
    if not u:
        return False
    with db() as c:
        row = c.execute("SELECT status FROM users WHERE telegram_id=?", (u.id,)).fetchone()
        if not row:
            c.execute("INSERT INTO users(telegram_id, username, status) VALUES (?, ?, 'pending')", (u.id, u.username or ""))
            await context.bot.send_message(ADMIN_ID, f"🔔 Yeni kullanıcı onayı bekliyor\nID: `{u.id}`\nKullanıcı: @{u.username or 'yok'}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Onayla", callback_data=f"approve:{u.id}"), InlineKeyboardButton("❌ Reddet", callback_data=f"reject:{u.id}")]]))
        elif row["status"] == "rejected":
            await update.effective_message.reply_text("Bu kullanıcı reddedildi.")
            return False
    if not approved(u.id):
        await update.effective_message.reply_text("⏳ Admin onayı bekleniyor. Onaylandığında botu kullanabilirsiniz.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ensure_user(update, context):
        await update.message.reply_text("📣 *Telegram Otomatik Duyuru Botu*\n\nAşağıdaki menüden işlem seçin.", parse_mode=ParseMode.MARKDOWN, reply_markup=menu())


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    if data.startswith("approve:") or data.startswith("reject:"):
        if uid != ADMIN_ID:
            return
        target = int(data.split(":")[1]); status = "approved" if data.startswith("approve") else "rejected"
        with db() as c: c.execute("UPDATE users SET status=? WHERE telegram_id=?", (status, target))
        await q.edit_message_text(("✅ Kullanıcı onaylandı." if status == "approved" else "❌ Kullanıcı reddedildi.") + f"\nID: {target}")
        try: await context.bot.send_message(target, "✅ Admin onayladı. /start komutuyla botu kullanabilirsiniz." if status == "approved" else "❌ Kullanıcı başvurunuz reddedildi.")
        except Exception: pass
        return
    if not approved(uid):
        await q.edit_message_text("⏳ Admin onayı bekleniyor."); return
    if data == "home": await q.edit_message_text("📣 Ana menü", reply_markup=menu())
    elif data == "account_add":
        await q.edit_message_text("Telefon numaranızı uluslararası formatta gönderin. Örnek: +905xxxxxxxxx", reply_markup=back_menu()); return PHONE
    elif data == "groups_import": await show_import_groups(q, uid)
    elif data == "groups_delete": await show_delete_groups(q, uid)
    elif data == "group_link": await q.edit_message_text("Grup bağlantısını gönderin. Örnek: https://t.me/grupkullaniciadi veya https://t.me/+davetkodu", reply_markup=back_menu()); return GROUP_LINK
    elif data == "announcement":
        with db() as c: row = c.execute("SELECT announcement FROM settings WHERE user_id=?", (uid,)).fetchone()
        text = row["announcement"] if row and row["announcement"] else "Henüz duyuru mesajı ayarlanmadı."
        await q.edit_message_text(f"Mevcut mesaj:\n\n{text}\n\nYeni mesaj için aşağıdaki butona basın.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Yeni mesaj ayarla", callback_data="announcement_set"), InlineKeyboardButton("🗑 Sil", callback_data="announcement_clear")], [InlineKeyboardButton("⬅️ Ana menü", callback_data="home")]]))
    elif data == "announcement_set": await q.edit_message_text("Duyuru metnini gönderin.", reply_markup=back_menu()); return ANNOUNCEMENT
    elif data == "announcement_clear":
        with db() as c: c.execute("INSERT INTO settings(user_id, announcement) VALUES (?, NULL) ON CONFLICT(user_id) DO UPDATE SET announcement=NULL", (uid,))
        await q.edit_message_text("✅ Duyuru mesajı silindi.", reply_markup=menu())
    elif data == "interval":
        with db() as c: row = c.execute("SELECT interval_minutes FROM settings WHERE user_id=?", (uid,)).fetchone()
        current = row["interval_minutes"] if row else DEFAULT_INTERVAL
        await q.edit_message_text(f"Mevcut sıklık: *{current} dakika*\n\nYeni süreyi dakika olarak gönderin.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_menu()); return INTERVAL
    elif data == "accounts": await show_accounts(q, uid)
    elif data == "status": await show_status(q, uid)
    elif data == "users" and uid == ADMIN_ID: await show_users(q)
    elif data.startswith("delete_group:"):
        gid = int(data.split(":")[1])
        with db() as c: c.execute("DELETE FROM groups WHERE id=? AND user_id=?", (gid, uid))
        await q.edit_message_text("✅ Grup silindi.", reply_markup=menu())
    elif data == "delete_all_groups":
        with db() as c: c.execute("DELETE FROM groups WHERE user_id=?", (uid,))
        await q.edit_message_text("✅ Tüm gruplar silindi.", reply_markup=menu())
    elif data == "import_all":
        await import_all(q, uid)
    return ConversationHandler.END


async def account_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip(); uid = update.effective_user.id
    client = TelegramClient(str(SESSION_DIR / f"user_{uid}"), API_ID, API_HASH)
    try:
        await client.connect(); result = await client.send_code_request(phone)
        context.user_data.update(phone=phone, phone_code_hash=result.phone_code_hash, pending_client=client)
        await update.message.reply_text("Telegram doğrulama kodunu gönderin.", reply_markup=back_menu()); return CODE
    except PhoneNumberInvalidError: await update.message.reply_text("❌ Telefon numarası geçersiz. Tekrar deneyin."); await client.disconnect(); return PHONE
    except Exception as e: log.exception(e); await update.message.reply_text("❌ Kod gönderilemedi."); await client.disconnect(); return ConversationHandler.END


async def account_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = context.user_data.get("pending_client"); uid = update.effective_user.id
    if not client: await update.message.reply_text("Oturum bulunamadı, yeniden başlayın."); return ConversationHandler.END
    try:
        await client.sign_in(phone=context.user_data["phone"], code=update.message.text.strip(), phone_code_hash=context.user_data["phone_code_hash"])
    except SessionPasswordNeededError:
        await update.message.reply_text("Bu hesapta iki aşamalı doğrulama açık. Telegram şifrenizi gönderin."); return PASSWORD
    except PhoneCodeInvalidError: await update.message.reply_text("❌ Kod geçersiz. Tekrar gönderin."); return CODE
    except Exception: await update.message.reply_text("❌ Giriş başarısız."); await client.disconnect(); return ConversationHandler.END
    await save_account(uid, context.user_data["phone"], client)
    await update.message.reply_text("✅ Telegram hesabı eklendi.", reply_markup=menu()); return ConversationHandler.END


async def account_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = context.user_data.get("pending_client"); uid = update.effective_user.id
    try: await client.sign_in(password=update.message.text.strip())
    except Exception: await update.message.reply_text("❌ Şifre hatalı."); return PASSWORD
    await save_account(uid, context.user_data["phone"], client)
    await update.message.reply_text("✅ Telegram hesabı eklendi.", reply_markup=menu()); return ConversationHandler.END


async def save_account(uid, phone, client):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO accounts(user_id, phone, session_name) VALUES (?, ?, ?)", (uid, phone, f"user_{uid}"))
        account = c.execute("SELECT id FROM accounts WHERE user_id=? AND session_name=?", (uid, f"user_{uid}")).fetchone()
    clients[uid] = client


async def get_client(uid: int) -> Optional[TelegramClient]:
    if uid in clients and clients[uid].is_connected(): return clients[uid]
    with db() as c: row = c.execute("SELECT session_name FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (uid,)).fetchone()
    if not row: return None
    client = TelegramClient(str(SESSION_DIR / row["session_name"]), API_ID, API_HASH); await client.connect()
    if not await client.is_user_authorized(): return None
    clients[uid] = client; return client


async def show_import_groups(q, uid):
    client = await get_client(uid)
    if not client: await q.edit_message_text("Önce Telegram hesabı ekleyin.", reply_markup=menu()); return
    found = []
    async for d in client.iter_dialogs():
        if d.is_group or (isinstance(d.entity, Channel) and getattr(d.entity, "megagroup", False)):
            found.append((d.id, d.name, getattr(d.entity, "username", None)))
    context = q._bot.get("_application") if False else None
    # Store a short-lived result in the global in-memory map for the callback.
    group_cache[uid] = found
    rows = [[InlineKeyboardButton(f"{i+1}. {name[:35]}", callback_data=f"noop:{i}")] for i, (_, name, _) in enumerate(found[:40])]
    rows.append([InlineKeyboardButton("✅ Hepsini ekle", callback_data="import_all"), InlineKeyboardButton("İptal", callback_data="home")])
    await q.edit_message_text(f"{len(found)} grup bulundu. Hepsini eklemek için onaylayın.", reply_markup=InlineKeyboardMarkup(rows))


group_cache: dict[int, list] = {}


async def import_all(q, uid):
    found = group_cache.get(uid, [])
    with db() as c:
        for chat_id, title, username in found:
            c.execute("INSERT OR IGNORE INTO groups(user_id, account_id, chat_id, title, username) SELECT ?, id, ?, ?, ? FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (uid, chat_id, title, username, uid))
    await q.edit_message_text(f"✅ {len(found)} grup eklendi.", reply_markup=menu())


async def show_delete_groups(q, uid):
    with db() as c: rows = c.execute("SELECT id,title FROM groups WHERE user_id=? ORDER BY title", (uid,)).fetchall()
    buttons = [[InlineKeyboardButton(f"🗑 {r['title'][:40]}", callback_data=f"delete_group:{r['id']}")] for r in rows[:80]]
    buttons.append([InlineKeyboardButton("🗑 Tümünü sil", callback_data="delete_all_groups"), InlineKeyboardButton("⬅️ Ana menü", callback_data="home")])
    await q.edit_message_text(f"Kayıtlı grup sayısı: {len(rows)}", reply_markup=InlineKeyboardMarkup(buttons))


async def group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; client = await get_client(uid)
    if not client: await update.message.reply_text("Önce Telegram hesabı ekleyin.", reply_markup=menu()); return ConversationHandler.END
    try:
        entity = await client.get_entity(update.message.text.strip())
        if not isinstance(entity, (Channel, Chat)): raise ValueError
        username = getattr(entity, "username", None); title = getattr(entity, "title", str(entity.id))
        with db() as c: c.execute("INSERT OR IGNORE INTO groups(user_id,account_id,chat_id,title,username) SELECT ?,id,?,?,? FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (uid, entity.id, title, username, uid))
        await update.message.reply_text(f"✅ Grup eklendi: {title}", reply_markup=menu())
    except Exception: await update.message.reply_text("❌ Grup bulunamadı veya hesaba erişim yok.", reply_markup=menu())
    return ConversationHandler.END


async def announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c: c.execute("INSERT INTO settings(user_id,announcement) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET announcement=excluded.announcement", (update.effective_user.id, update.message.text))
    await update.message.reply_text("✅ Duyuru mesajı kaydedildi.", reply_markup=menu()); return ConversationHandler.END


async def interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: minutes = int(update.message.text.strip()); assert 1 <= minutes <= 10080
    except Exception: await update.message.reply_text("1 ile 10080 arasında bir dakika değeri gönderin."); return INTERVAL
    with db() as c: c.execute("INSERT INTO settings(user_id,interval_minutes) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET interval_minutes=excluded.interval_minutes", (update.effective_user.id, minutes))
    await update.message.reply_text(f"✅ Duyuru sıklığı {minutes} dakika olarak ayarlandı.", reply_markup=menu()); return ConversationHandler.END


async def show_accounts(q, uid):
    with db() as c: rows = c.execute("SELECT phone,created_at FROM accounts WHERE user_id=?", (uid,)).fetchall()
    text = "\n".join(f"• {r['phone']} ({r['created_at']})" for r in rows) or "Hesap yok."
    await q.edit_message_text(f"Telegram hesapları:\n{text}", reply_markup=back_menu())


async def show_status(q, uid):
    with db() as c:
        g = c.execute("SELECT COUNT(*) n FROM groups WHERE user_id=?", (uid,)).fetchone()["n"]
        s = c.execute("SELECT announcement,interval_minutes FROM settings WHERE user_id=?", (uid,)).fetchone()
    await q.edit_message_text(f"Gruplar: {g}\nDuyuru: {'ayarlı' if s and s['announcement'] else 'ayarlı değil'}\nSıklık: {s['interval_minutes'] if s else DEFAULT_INTERVAL} dakika", reply_markup=back_menu())


async def show_users(q):
    with db() as c: rows = c.execute("SELECT telegram_id,username,status FROM users ORDER BY created_at DESC").fetchall()
    text = "\n".join(f"{r['telegram_id']} @{r['username'] or '-'} — {r['status']}" for r in rows) or "Kullanıcı yok."
    buttons = [[InlineKeyboardButton(f"🗑 {r['telegram_id']}", callback_data=f"remove_user:{r['telegram_id']}")] for r in rows if r['telegram_id'] != ADMIN_ID]
    buttons.append([InlineKeyboardButton("⬅️ Ana menü", callback_data="home")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def remove_user_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID: await q.answer(); return
    await q.answer(); uid = int(q.data.split(":")[1])
    with db() as c: c.execute("DELETE FROM users WHERE telegram_id=? AND telegram_id<>?", (uid, ADMIN_ID))
    await q.edit_message_text("✅ Kullanıcı silindi.", reply_markup=menu())


async def announce_job(context: ContextTypes.DEFAULT_TYPE):
    with db() as c: settings = c.execute("SELECT user_id,announcement,interval_minutes FROM settings WHERE announcement IS NOT NULL AND announcement<>''").fetchall()
    for s in settings:
        uid = s["user_id"]; client = await get_client(uid)
        if not client: continue
        with db() as c: groups = c.execute("SELECT chat_id,title FROM groups WHERE user_id=?", (uid,)).fetchall()
        for g in groups:
            try: await client.send_message(g["chat_id"], s["announcement"])
            except Exception as e: log.warning("Send failed %s: %s", g["title"], e)


async def setup_job(application):
    application.job_queue.run_repeating(announce_job, interval=60, first=30, name="announcement-loop")


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(setup_job).build()
    conv = ConversationHandler(entry_points=[CallbackQueryHandler(callback, pattern="^(account_add|group_link|announcement_set|interval)$")], states={PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, account_phone)], CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, account_code)], PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, account_password)], ANNOUNCEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, announcement)], INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, interval)], GROUP_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_link)]}, fallbacks=[CommandHandler("start", start)], per_user=True, per_chat=True)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(remove_user_callback, pattern="^remove_user:"))
    app.add_handler(CallbackQueryHandler(callback))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__": main()
