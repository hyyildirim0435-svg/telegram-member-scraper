import os
import json
import base64
import asyncio
import time
import threading
import requests
from datetime import datetime
from flask import Flask, request
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import StringSession
from cryptography.fernet import Fernet, InvalidToken
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
AUTHORIZED_ADMIN_ID = 8302545787
ADMIN_ID = int(os.getenv("ADMIN_ID", str(AUTHORIZED_ADMIN_ID)))
ADMIN_IDS = [AUTHORIZED_ADMIN_ID]
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-member-scraper.onrender.com")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "hyyildirim0435-svg/telegram-member-scraper")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
SESSIONS_JSON = os.getenv("SESSIONS_JSON", "")
SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "")
SESSION_BACKUP_FILE = "sessions.enc"

_persistence_lock = threading.Lock()


def _session_fernet():
    if not SESSION_ENCRYPTION_KEY:
        return None
    try:
        return Fernet(SESSION_ENCRYPTION_KEY.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        print(f"Encrypted session backup disabled: invalid SESSION_ENCRYPTION_KEY ({exc})")
        return None


# Data files
SESSIONS_FILE = "sessions.json"
MESSAGED_USERS_FILE = "messaged_users.json"
CONFIG_FILE = "config.json"

# Conversation states
(ADDING_PHONE, ADDING_CODE, ADDING_2FA,
 SETTING_SOURCE, SETTING_MESSAGE, SETTING_MSG_COUNT) = range(6)

# Flask app for health check
flask_app = Flask(__name__)
BOT_LOOP = None
BOT_APPLICATION = None


@flask_app.route("/")
def home():
    return "Bot is running!", 200


@flask_app.route("/health")
def health():
    return "OK", 200


@flask_app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    if TELEGRAM_WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != TELEGRAM_WEBHOOK_SECRET:
        return "Forbidden", 403
    if BOT_LOOP is None or BOT_APPLICATION is None:
        return "Bot is starting", 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return "Invalid update", 400
    try:
        update = Update.de_json(payload, BOT_APPLICATION.bot)
        future = asyncio.run_coroutine_threadsafe(
            BOT_APPLICATION.process_update(update), BOT_LOOP
        )
        future.result(timeout=20)
        return "OK", 200
    except Exception as exc:
        print(f"Webhook update error: {type(exc).__name__}: {exc}")
        return "Update processing failed", 500


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def keep_alive():
    url = RENDER_EXTERNAL_URL or f"http://localhost:{PORT}"
    while True:
        time.sleep(600)
        try:
            requests.get(f"{url}/health", timeout=10)
            print(f"[Keep-Alive] Ping sent at {datetime.now().strftime('%H:%M:%S')}")
        except:
            pass


def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_from_github(filepath):
    if not GITHUB_TOKEN:
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}"
        response = requests.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15)
        if response.status_code != 200:
            return None
        encoded = response.json().get("content", "").replace("\\n", "")
        return json.loads(base64.b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        print(f"GitHub load error for {filepath}: {exc}")
        return None


def save_to_github(filepath, data):
    if not GITHUB_TOKEN or filepath == SESSIONS_FILE:
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}"
        content = base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        with _persistence_lock:
            current = requests.get(
                url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15
            )
            payload = {
                "message": f"Update {filepath}",
                "content": content,
                "branch": GITHUB_BRANCH,
            }
            if current.status_code == 200:
                payload["sha"] = current.json().get("sha")
            response = requests.put(url, headers=_github_headers(), json=payload, timeout=20)
            if response.status_code not in (200, 201):
                print(f"GitHub save error for {filepath}: {response.status_code} {response.text[:300]}")
    except Exception as exc:
        print(f"GitHub save error for {filepath}: {exc}")


def load_encrypted_sessions():
    if not GITHUB_TOKEN:
        return None
    try:
        fernet = _session_fernet()
        if fernet is None:
            return None
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{SESSION_BACKUP_FILE}"
        response = requests.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15)
        if response.status_code != 200:
            return None
        encoded = response.json().get("content", "").replace("\n", "")
        encrypted = base64.b64decode(encoded)
        decrypted = fernet.decrypt(encrypted)
        data = json.loads(decrypted.decode("utf-8"))
        return data if isinstance(data, list) else None
    except InvalidToken:
        print("Encrypted session backup cannot be decrypted; falling back.")
        return None
    except Exception as exc:
        print(f"Encrypted session load error: {exc}")
        return None


def save_encrypted_sessions(data):
    if not GITHUB_TOKEN:
        return
    try:
        fernet = _session_fernet()
        if fernet is None:
            return
        encrypted = fernet.encrypt(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        )
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{SESSION_BACKUP_FILE}"
        content = base64.b64encode(encrypted).decode("ascii")
        with _persistence_lock:
            current = requests.get(
                url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15
            )
            payload = {
                "message": "Update encrypted Telegram sessions",
                "content": content,
                "branch": GITHUB_BRANCH,
            }
            if current.status_code == 200:
                payload["sha"] = current.json().get("sha")
            response = requests.put(url, headers=_github_headers(), json=payload, timeout=20)
            if response.status_code not in (200, 201):
                print(f"Encrypted session save error: {response.status_code} {response.text[:300]}")
    except Exception as exc:
        print(f"Encrypted session save error: {exc}")


def load_json(filepath, default=None):
    if default is None:
        default = {}
    if filepath == SESSIONS_FILE:
        encrypted_sessions = load_encrypted_sessions()
        if encrypted_sessions is not None:
            try:
                with open(filepath, "w") as f:
                    json.dump(encrypted_sessions, f, indent=2, ensure_ascii=False)
            except OSError:
                pass
            return encrypted_sessions
        if SESSIONS_JSON:
            try:
                return json.loads(SESSIONS_JSON)
            except json.JSONDecodeError:
                print("SESSIONS_JSON is not valid JSON; falling back.")
    remote_data = load_from_github(filepath)
    if remote_data is not None:
        try:
            with open(filepath, "w") as f:
                json.dump(remote_data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        return remote_data
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    save_to_github(filepath, data)


def is_admin(user_id):
    return user_id in ADMIN_IDS


class BotState:
    def __init__(self):
        self.sessions = load_json(SESSIONS_FILE, [])
        if self.sessions and SESSION_ENCRYPTION_KEY and not load_encrypted_sessions():
            save_encrypted_sessions(self.sessions)
        self.messaged_users = load_json(MESSAGED_USERS_FILE, [])
        self.config = load_json(CONFIG_FILE, {
            "source_group": "",
            "message_text": "",
            "scanned_users": []
        })
        self.temp_phone = None
        self.temp_client = None
        self.temp_phone_hash = None
        self.operations = {}
        self.stop_events = {}

    def is_operation_running(self, chat_id):
        task = self.operations.get(str(chat_id))
        return bool(task and not task.done())

    def release_operation(self, chat_id):
        key = str(chat_id)
        self.operations.pop(key, None)
        self.stop_events.pop(key, None)

    def request_stop(self, chat_id):
        event = self.stop_events.get(str(chat_id))
        if not event or not self.is_operation_running(chat_id):
            return False
        event.set()
        return True

    def is_stop_requested(self, chat_id):
        event = self.stop_events.get(str(chat_id))
        return bool(event and event.is_set())

    def save_sessions(self):
        with open(SESSIONS_FILE, "w") as f:
            json.dump(self.sessions, f, indent=2, ensure_ascii=False)
        save_encrypted_sessions(self.sessions)

    def save_messaged_users(self):
        save_json(MESSAGED_USERS_FILE, self.messaged_users)

    def save_config(self):
        save_json(CONFIG_FILE, self.config)


state = BotState()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu botu kullanma yetkiniz yok.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📱 Hesap Ekle", callback_data="add_account")],
        [InlineKeyboardButton("📋 Hesapları Listele / Sil", callback_data="list_accounts")],
        [InlineKeyboardButton("🔗 Kaynak Grup Ayarla", callback_data="set_source")],
        [InlineKeyboardButton("🔍 Üye Tara", callback_data="scan_start")],
        [InlineKeyboardButton("✉️ Mesaj Ekle", callback_data="set_message")],
        [InlineKeyboardButton("🚀 Mesaj Gönder", callback_data="start_messaging")],
        [InlineKeyboardButton("⏹ Durdur", callback_data="stop_messaging")],
        [InlineKeyboardButton("📊 Durum", callback_data="status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_preview = state.config.get("message_text", "")
    if msg_preview and len(msg_preview) > 50:
        msg_preview = msg_preview[:50] + "..."

    text = (
        "🤖 <b>Telegram Mesaj Gönderme Botu</b>\n\n"
        f"📱 Kayıtlı Hesap: {len(state.sessions)}\n"
        f"🔗 Kaynak Grup: {state.config.get('source_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
        f"👥 Taranan Kullanıcı: {len(state.config.get('scanned_users', []))}\n"
        f"✉️ Mesaj: {msg_preview or 'Ayarlanmadı'}\n"
        f"✅ Mesaj Gönderilen: {len(state.messaged_users)}\n"
    )

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return ConversationHandler.END


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.edit_text("⛔ Bu botu kullanma yetkiniz yok.")
        return ConversationHandler.END

    data = query.data

    if data == "add_account":
        await query.message.edit_text(
            "📱 <b>Hesap Ekleme</b>\n\n"
            "Telefon numarasını girin (ülke kodu ile):\n"
            "Örnek: +905551234567\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return ADDING_PHONE

    elif data in ("list_accounts", "delete_account"):
        if not state.sessions:
            await query.message.edit_text("📋 Kayıtlı hesap yok.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        text = "📋 <b>Kayıtlı Hesaplar</b>\n\nSilmek istediğiniz hesabın düğmesine basın:\n"
        keyboard = []
        for i, session_data in enumerate(state.sessions):
            phone = session_data.get("phone", f"Hesap {i + 1}")
            text += f"{i + 1}. {phone}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 {i + 1}. {phone} hesabını sil", callback_data=f"delete_account:{i}")])
        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data="back_menu")])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ConversationHandler.END

    elif data.startswith("delete_account:"):
        try:
            index = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.message.edit_text("❌ Geçersiz hesap seçimi.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        if index < 0 or index >= len(state.sessions):
            await query.message.edit_text("⚠️ Bu hesap artık mevcut değil.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        removed = state.sessions.pop(index)
        state.save_sessions()
        await query.message.edit_text(
            f"✅ <b>{removed.get('phone', 'Telegram hesabı')}</b> silindi.\n"
            f"📱 Kalan hesap: {len(state.sessions)}\n\n/start ile menüye dön.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    elif data == "set_source":
        await query.message.edit_text(
            "🔗 <b>Kaynak Grup Ayarlama</b>\n\n"
            "Kullanıcıların taranacağı grubun linkini girin:\n"
            "Örnek: https://t.me/grupadi veya @grupadi\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_SOURCE

    elif data == "set_message":
        await query.message.edit_text(
            "✉️ <b>Mesaj Ayarlama</b>\n\n"
            "Kullanıcılara gönderilecek mesajı yazın:\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_MESSAGE

    elif data == "scan_start":
        if not state.config.get("source_group"):
            await query.message.edit_text("❌ Önce kaynak grup ayarlayın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.sessions:
            await query.message.edit_text("❌ Önce en az bir hesap ekleyin.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton("👥 Tüm Üyeleri Tara", callback_data="scan_members")],
            [InlineKeyboardButton("💬 Mesaj Atanları Tara", callback_data="scan_messages")],
            [InlineKeyboardButton("🔙 Geri", callback_data="back_menu")],
        ]
        await query.message.edit_text(
            "🔍 <b>Tarama Türü Seçin:</b>\n\n"
            "👥 <b>Tüm Üyeleri Tara:</b> Gruptaki tüm üyeleri tarar\n"
            "💬 <b>Mesaj Atanları Tara:</b> Grupta mesaj atan benzersiz kullanıcıları tarar",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    elif data == "scan_members":
        await query.message.edit_text("🔍 Tüm üyeler taranıyor... Lütfen bekleyin.")
        asyncio.create_task(scan_group_members(query.message, scan_type="members"))
        return ConversationHandler.END

    elif data == "scan_messages":
        await query.message.edit_text("🔍 Mesaj atanlar taranıyor... Lütfen bekleyin.")
        asyncio.create_task(scan_group_members(query.message, scan_type="messages"))
        return ConversationHandler.END

    elif data == "start_messaging":
        if not state.config.get("scanned_users"):
            await query.message.edit_text("❌ Önce tarama yapın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.config.get("message_text"):
            await query.message.edit_text("❌ Önce mesaj ayarlayın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.sessions:
            await query.message.edit_text("❌ Önce en az bir hesap ekleyin.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        available = [u for u in state.config["scanned_users"] if u not in state.messaged_users]
        await query.message.edit_text(
            f"🚀 <b>Mesaj Gönderme</b>\n\n"
            f"Toplam taranan: {len(state.config['scanned_users'])}\n"
            f"Daha önce mesaj gönderilen: {len(state.messaged_users)}\n"
            f"Mesaj gönderilebilir: {len(available)}\n\n"
            f"Kaç kişiye mesaj göndermek istiyorsunuz? (Sayı girin)\n\n"
            f"/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_MSG_COUNT

    elif data == "stop_messaging":
        if state.request_stop(query.message.chat_id):
            await query.message.edit_text(
                "⏹ <b>Durdurma isteği alındı.</b>\n\n"
                "İşlem güvenli noktada duracak ve rapor gönderilecek.",
                parse_mode="HTML"
            )
        else:
            await query.message.edit_text("ℹ️ Devam eden bir mesaj gönderme işlemi yok.\n\n/start ile menüye dön.")
        return ConversationHandler.END

    elif data == "status":
        available = [u for u in state.config.get("scanned_users", []) if u not in state.messaged_users]
        msg_preview = state.config.get("message_text", "")
        if msg_preview and len(msg_preview) > 100:
            msg_preview = msg_preview[:100] + "..."
        text = (
            "📊 <b>Durum Raporu</b>\n\n"
            f"📱 Kayıtlı Hesap: {len(state.sessions)}\n"
            f"🔗 Kaynak Grup: {state.config.get('source_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
            f"👥 Taranan Kullanıcı: {len(state.config.get('scanned_users', []))}\n"
            f"✉️ Mesaj: {msg_preview or 'Ayarlanmadı'}\n"
            f"✅ Mesaj Gönderilen: {len(state.messaged_users)}\n"
            f"⏳ Gönderilebilir: {len(available)}\n"
            f"🔄 İşlem Durumu: {'Çalışıyor' if state.is_operation_running(query.message.chat_id) else 'Boşta'}\n"
            f"\n/start ile menüye dön."
        )
        await query.message.edit_text(text, parse_mode="HTML")
        return ConversationHandler.END

    elif data == "back_menu":
        await start(update, context)
        return ConversationHandler.END

    return ConversationHandler.END


async def add_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    state.temp_phone = phone

    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        result = await client.send_code_request(phone)
        state.temp_client = client
        state.temp_phone_hash = result.phone_code_hash

        await update.message.reply_text(
            f"📲 <b>{phone}</b> numarasına kod gönderildi.\n\n"
            "Gelen kodu girin:\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return ADDING_CODE

    except errors.FloodWaitError as e:
        await update.message.reply_text(f"⚠️ Flood hatası. {e.seconds} saniye bekleyin.\n\n/start ile menüye dön.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}\n\n/start ile menüye dön.")
        return ConversationHandler.END


async def add_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()

    try:
        await state.temp_client.sign_in(
            state.temp_phone,
            code,
            phone_code_hash=state.temp_phone_hash
        )

        session_string = state.temp_client.session.save()
        state.sessions.append({
            "phone": state.temp_phone,
            "session_string": session_string
        })
        state.save_sessions()

        await state.temp_client.disconnect()
        state.temp_client = None

        await update.message.reply_text(
            f"✅ <b>{state.temp_phone}</b> hesabı başarıyla eklendi!\n"
            f"📱 Toplam hesap: {len(state.sessions)}\n\n"
            "/start ile menüye dön.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    except errors.SessionPasswordNeededError:
        await update.message.reply_text(
            "🔐 Bu hesapta 2FA aktif.\n"
            "2FA şifrenizi girin:\n\n"
            "/iptal yazarak iptal edebilirsiniz."
        )
        return ADDING_2FA

    except errors.PhoneCodeInvalidError:
        await update.message.reply_text("❌ Geçersiz kod. Tekrar deneyin:")
        return ADDING_CODE

    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}\n\n/start ile menüye dön.")
        if state.temp_client:
            await state.temp_client.disconnect()
        return ConversationHandler.END


async def add_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()

    try:
        await state.temp_client.sign_in(password=password)

        session_string = state.temp_client.session.save()
        state.sessions.append({
            "phone": state.temp_phone,
            "session_string": session_string
        })
        state.save_sessions()

        await state.temp_client.disconnect()
        state.temp_client = None

        await update.message.reply_text(
            f"✅ <b>{state.temp_phone}</b> hesabı başarıyla eklendi! (2FA ile)\n"
            f"📱 Toplam hesap: {len(state.sessions)}\n\n"
            "/start ile menüye dön.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    except errors.PasswordHashInvalidError:
        await update.message.reply_text("❌ Yanlış şifre. Tekrar deneyin:")
        return ADDING_2FA

    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}\n\n/start ile menüye dön.")
        if state.temp_client:
            await state.temp_client.disconnect()
        return ConversationHandler.END


async def set_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    state.config["source_group"] = link
    state.save_config()
    await update.message.reply_text(
        f"✅ Kaynak grup ayarlandı: <b>{link}</b>\n\n/start ile menüye dön.",
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def set_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    state.config["message_text"] = msg_text
    state.save_config()
    await update.message.reply_text(
        f"✅ Mesaj ayarlandı:\n\n<i>{msg_text}</i>\n\n/start ile menüye dön.",
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def set_msg_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Geçerli bir sayı girin:")
        return SETTING_MSG_COUNT

    chat_id = update.effective_chat.id

    if state.is_operation_running(chat_id):
        await update.message.reply_text("⚠️ Zaten bir işlem devam ediyor.\n\n/start ile menüye dön.")
        return ConversationHandler.END

    if count <= 0:
        await update.message.reply_text("❌ 0'dan büyük bir sayı girin:")
        return SETTING_MSG_COUNT

    state.stop_events[str(chat_id)] = asyncio.Event()
    task = asyncio.create_task(send_messages_task(update.message, count, chat_id))
    state.operations[str(chat_id)] = task

    await update.message.reply_text(
        f"🚀 Mesaj gönderme başlatılıyor... ({count} kişi)\n"
        "İşlem arka planda devam edecek."
    )
    return ConversationHandler.END


async def stop_messaging(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu botu kullanma yetkiniz yok.")
        return ConversationHandler.END
    if state.request_stop(update.effective_chat.id):
        await update.message.reply_text(
            "⏹ Durdurma isteği alındı. İşlem güvenli noktada duracak ve rapor gönderilecek."
        )
    else:
        await update.message.reply_text("ℹ️ Devam eden bir mesaj gönderme işlemi yok.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ İşlem iptal edildi.\n\n/start ile menüye dön.")
    if state.temp_client:
        await state.temp_client.disconnect()
        state.temp_client = None
    return ConversationHandler.END


async def get_client(session_data):
    client = TelegramClient(
        StringSession(session_data["session_string"]),
        API_ID, API_HASH
    )
    await client.connect()
    if not await client.is_user_authorized():
        return None
    return client


async def resolve_group(client, group_link):
    link = group_link.strip()
    if link.startswith("https://t.me/"):
        link = "@" + link.split("https://t.me/")[1].split("/")[0]
    elif link.startswith("t.me/"):
        link = "@" + link.split("t.me/")[1].split("/")[0]

    if "+" in group_link or "joinchat" in group_link:
        hash_part = group_link.split("/")[-1].replace("+", "")
        try:
            await client(functions.messages.ImportChatInviteRequest(hash=hash_part))
        except errors.UserAlreadyParticipantError:
            pass
        except:
            pass
        result = await client(functions.messages.CheckChatInviteRequest(hash=hash_part))
        if hasattr(result, 'chat'):
            return result.chat

    entity = await client.get_entity(link)
    return entity


async def scan_group_members(message, scan_type="messages"):
    try:
        session_data = state.sessions[0]
        client = await get_client(session_data)
        if not client:
            await message.edit_text("❌ Hesap oturumu geçersiz. Tekrar ekleyin.\n\n/start ile menüye dön.")
            return

        group = await resolve_group(client, state.config["source_group"])
        users = set()

        MAX_USERS = 2000

        if scan_type == "members":
            await message.edit_text("🔍 Tüm üyeler taranıyor... (max 2000)")
            async for user in client.iter_participants(group):
                if user.username and not user.bot:
                    users.add(user.username)
                if len(users) >= MAX_USERS:
                    break
        else:
            await message.edit_text("🔍 Mesaj atanlar taranıyor... (max 2000)")
            msg_count = 0
            async for msg in client.iter_messages(group, limit=10000):
                msg_count += 1
                sender = msg.sender
                if isinstance(sender, types.User) and sender.username and not sender.bot:
                    users.add(sender.username)
                if len(users) >= MAX_USERS:
                    break
                if msg_count % 1000 == 0:
                    try:
                        await message.edit_text(f"🔍 Mesaj atanlar taranıyor... {len(users)} kullanıcı bulundu ({msg_count} mesaj tarandı)")
                    except:
                        pass

        state.config["scanned_users"] = list(users)
        state.save_config()

        await client.disconnect()

        already_messaged = [u for u in users if u in state.messaged_users]
        available = [u for u in users if u not in state.messaged_users]

        scan_type_text = "Tüm Üyeler" if scan_type == "members" else "Mesaj Atanlar"
        await message.edit_text(
            f"✅ <b>Tarama Tamamlandı!</b>\n\n"
            f"📋 Tarama Türü: {scan_type_text}\n"
            f"👥 Toplam Benzersiz Kullanıcı: {len(users)}\n"
            f"✅ Daha Önce Mesaj Gönderilmiş: {len(already_messaged)}\n"
            f"⏳ Mesaj Gönderilebilir: {len(available)}\n\n"
            f"/start ile menüye dön.",
            parse_mode="HTML"
        )

    except Exception as e:
        await message.edit_text(f"❌ Tarama hatası: {str(e)}\n\n/start ile menüye dön.")


async def send_messages_task(message, count, chat_id):
    """Send messages to scanned users using round-robin account rotation."""
    report = {
        "total_requested": count,
        "sent": 0,
        "skipped_already_messaged": 0,
        "errors": 0,
        "error_accounts": [],
        "sent_usernames": [],
        "failed_usernames": [],
        "stopped": False,
        "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    available_users = [u for u in state.config["scanned_users"] if u not in state.messaged_users]

    if not available_users:
        await message.reply_text("❌ Mesaj gönderilebilir kullanıcı kalmadı.\n\n/start ile menüye dön.")
        state.release_operation(chat_id)
        return

    target_count = min(count, len(available_users))
    users_to_message = available_users[:target_count]
    msg_text = state.config.get("message_text", "")

    # Connect accounts
    clients = []
    active_sessions = []
    now = time.time()
    for session_data in state.sessions:
        if float(session_data.get("disabled_until", 0) or 0) > now:
            continue
        try:
            c = await get_client(session_data)
            if c:
                clients.append(c)
                active_sessions.append(session_data)
        except Exception as exc:
            print(f"Account connection error ({session_data.get('phone', 'unknown')}): {exc}")

    if not clients:
        await message.reply_text("❌ Hiçbir hesap bağlanamadı.\n\n/start ile menüye dön.")
        state.release_operation(chat_id)
        return

    num_accounts = len(clients)
    disabled_indices = set()

    await message.reply_text(
        f"🚀 <b>Mesaj gönderme başladı!</b>\n\n"
        f"📱 Aktif Hesap Sayısı: {num_accounts}\n"
        f"🔄 Mod: Sırayla 1'er 1'er (round-robin)\n"
        f"🎯 Hedef: {target_count} kişi\n"
        f"⏱ Her mesaj arası: 60 saniye\n"
        f"⏱ Tahmini süre: ~{target_count} dakika",
        parse_mode="HTML"
    )

    try:
        rotation_idx = 0

        for i, username in enumerate(users_to_message):
            if state.is_stop_requested(chat_id):
                report["stopped"] = True
                break

            # Skip already messaged
            if username in state.messaged_users:
                report["skipped_already_messaged"] += 1
                continue

            # Find next available account
            attempts = 0
            while rotation_idx % num_accounts in disabled_indices and attempts < num_accounts:
                rotation_idx += 1
                attempts += 1

            if attempts >= num_accounts:
                await message.reply_text("❌ Kullanılabilir hesap kalmadı! İşlem durduruluyor.")
                break

            current_idx = rotation_idx % num_accounts
            client = clients[current_idx]

            try:
                # Get user entity and send message
                user_entity = await client.get_entity(f"@{username}")
                await client.send_message(user_entity, msg_text)

                state.messaged_users.append(username)
                state.save_messaged_users()
                report["sent"] += 1
                report["sent_usernames"].append(username)

                # Progress update every 10 users
                if report["sent"] % 10 == 0:
                    active_count = num_accounts - len(disabled_indices)
                    await message.reply_text(
                        f"📊 İlerleme: {report['sent']}/{target_count}\n"
                        f"📱 Aktif Hesap: {active_sessions[current_idx]['phone']}\n"
                        f"🔄 Kullanılabilir Hesap: {active_count}/{num_accounts}"
                    )

            except (errors.UserPrivacyRestrictedError,
                    errors.InputUserDeactivatedError) as e:
                report["errors"] += 1
                report["failed_usernames"].append(f"@{username}: {type(e).__name__}")
                # Bu kullanıcıya bir daha mesaj atılmasın
                state.messaged_users.append(username)
                state.save_messaged_users()
                rotation_idx += 1
                continue

            except errors.FloodWaitError as e:
                await message.reply_text(
                    f"⚠️ Flood hatası! {active_sessions[current_idx]['phone']}\n"
                    f"⏱ {e.seconds} saniye bekleme gerekiyor."
                )
                if e.seconds > 300:
                    disabled_indices.add(current_idx)
                    active_sessions[current_idx]["disabled_until"] = time.time() + max(e.seconds, 300)
                    report["error_accounts"].append(active_sessions[current_idx]['phone'])
                    state.save_sessions()
                else:
                    await asyncio.sleep(e.seconds)

            except (errors.PeerFloodError, errors.ChatWriteForbiddenError) as e:
                error_name = type(e).__name__
                disabled_indices.add(current_idx)
                active_sessions[current_idx]["disabled_until"] = time.time() + 3600
                report["error_accounts"].append(active_sessions[current_idx]['phone'])
                state.save_sessions()
                await message.reply_text(
                    f"⚠️ <b>{active_sessions[current_idx]['phone']}</b> hata aldı! ({error_name})\n"
                    f"🔄 Kalan hesaplarla devam ediliyor...",
                    parse_mode="HTML"
                )

            except Exception as e:
                error_name = type(e).__name__
                error_str = str(e).lower()
                if "ban" in error_str or "restrict" in error_str or "flood" in error_str:
                    disabled_indices.add(current_idx)
                    active_sessions[current_idx]["disabled_until"] = time.time() + 3600
                    report["error_accounts"].append(active_sessions[current_idx]['phone'])
                    state.save_sessions()
                else:
                    report["errors"] += 1
                    report["failed_usernames"].append(f"@{username}: {error_name}")
                    # Bu kullanıcıya bir daha mesaj atılmasın
                    state.messaged_users.append(username)
                    state.save_messaged_users()

            # Move to next account
            rotation_idx += 1

            # Wait 60 seconds, but wake immediately on stop
            try:
                await asyncio.wait_for(state.stop_events[str(chat_id)].wait(), timeout=60)
                report["stopped"] = True
                break
            except asyncio.TimeoutError:
                pass

    except Exception as e:
        await message.reply_text(f"❌ Kritik hata: {str(e)}")

    finally:
        for c in clients:
            try:
                await c.disconnect()
            except:
                pass
        report["stopped"] = report["stopped"] or state.is_stop_requested(chat_id)
        state.release_operation(chat_id)

    # Final report
    report["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_text = (
        f"📊 <b>MESAJ GÖNDERME RAPORU</b>\n"
        f"{'═' * 30}\n\n"
        f"⏱ Başlangıç: {report['start_time']}\n"
        f"⏱ Bitiş: {report['end_time']}\n"
        f"📌 Durum: {'⏹ Durduruldu' if report['stopped'] else '✅ Tamamlandı'}\n\n"
        f"📋 <b>Özet:</b>\n"
        f"• İstenen: {report['total_requested']} kişi\n"
        f"• ✅ Başarıyla Gönderilen: {report['sent']} kişi\n"
        f"• ⏭ Atlanan (daha önce gönderilmiş): {report['skipped_already_messaged']} kişi\n"
        f"• ❌ Hatalı: {report['errors']} kişi\n"
        f"• 🚫 Hata Alan Hesaplar: {len(report['error_accounts'])}\n\n"
    )

    if report["error_accounts"]:
        report_text += f"🚫 <b>Hata Alan Hesaplar:</b>\n"
        for acc in report["error_accounts"]:
            report_text += f"  • {acc}\n"
        report_text += "\n"

    if report["sent_usernames"][:20]:
        report_text += f"✅ <b>Mesaj Gönderilen (ilk 20):</b>\n"
        for u in report["sent_usernames"][:20]:
            report_text += f"  • @{u}\n"
        if len(report["sent_usernames"]) > 20:
            report_text += f"  ... ve {len(report['sent_usernames']) - 20} kişi daha\n"
        report_text += "\n"

    if report["failed_usernames"][:10]:
        report_text += f"❌ <b>Başarısız (ilk 10):</b>\n"
        for u in report["failed_usernames"][:10]:
            report_text += f"  • {u}\n"
        report_text += "\n"

    report_text += f"\n/start ile menüye dön."

    await message.reply_text(report_text, parse_mode="HTML")


def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("durdur", stop_messaging),
            CallbackQueryHandler(button_handler),
        ],
        states={
            ADDING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phone)],
            ADDING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_code)],
            ADDING_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_2fa)],
            SETTING_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_source)],
            SETTING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_message)],
            SETTING_MSG_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_msg_count)],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv_handler)

    async def start_bot():
        global BOT_LOOP, BOT_APPLICATION
        BOT_LOOP = loop
        BOT_APPLICATION = app
        await app.initialize()
        await app.start()
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/telegram/webhook"
        await app.bot.set_webhook(
            url=webhook_url,
            secret_token=TELEGRAM_WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
        print(f"🤖 Bot başlatıldı! Webhook: {webhook_url}")
        while True:
            await asyncio.sleep(3600)

    try:
        loop.run_until_complete(start_bot())
    except Exception as e:
        print(f"Bot hatası: {e}")


def main():
    if not BOT_TOKEN:
        print("HATA: BOT_TOKEN ayarlanmamış!")
        return
    if not API_ID or not API_HASH:
        print("HATA: API_ID ve API_HASH ayarlanmamış!")
        return

    print(f"🤖 Bot başlatılıyor...")
    print(f"📱 Admin ID: {ADMIN_ID}")
    print(f"🔑 API ID: {API_ID}")

    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Health check server started on port {PORT}")

    time.sleep(2)

    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("💓 Keep-alive started (10 min interval)")

    # Run bot
    run_bot()


if __name__ == "__main__":
    main()
