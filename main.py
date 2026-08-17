import os
import json
import asyncio
import random
import time
import threading
import requests
from datetime import datetime
from flask import Flask
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import StringSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

# Data files
SESSIONS_FILE = "sessions.json"
ADDED_USERS_FILE = "added_users.json"
CONFIG_FILE = "config.json"

# Conversation states
(ADDING_PHONE, ADDING_CODE, ADDING_2FA,
 SETTING_SOURCE, SETTING_TARGET,
 SETTING_ADD_COUNT, CHOOSING_SCAN_TYPE) = range(7)

# Flask app for health check
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Bot is running!", 200


@flask_app.route("/health")
def health():
    return "OK", 200


def run_flask():
    """Run Flask in a separate thread."""
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def keep_alive():
    """Ping self every 10 minutes to prevent Render from sleeping."""
    url = RENDER_EXTERNAL_URL or f"http://localhost:{PORT}"
    while True:
        time.sleep(600)  # 10 minutes
        try:
            requests.get(f"{url}/health", timeout=10)
            print(f"[Keep-Alive] Ping sent at {datetime.now().strftime('%H:%M:%S')}")
        except:
            pass


def load_json(filepath, default=None):
    if default is None:
        default = {}
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_admin(user_id):
    return user_id == ADMIN_ID


class BotState:
    def __init__(self):
        self.sessions = load_json(SESSIONS_FILE, [])
        self.added_users = load_json(ADDED_USERS_FILE, [])
        self.config = load_json(CONFIG_FILE, {
            "source_group": "",
            "target_group": "",
            "scanned_users": []
        })
        self.temp_phone = None
        self.temp_client = None
        self.temp_phone_hash = None
        self.is_running = False

    def save_sessions(self):
        save_json(SESSIONS_FILE, self.sessions)

    def save_added_users(self):
        save_json(ADDED_USERS_FILE, self.added_users)

    def save_config(self):
        save_json(CONFIG_FILE, self.config)


state = BotState()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu botu kullanma yetkiniz yok.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📱 Hesap Ekle", callback_data="add_account")],
        [InlineKeyboardButton("📋 Hesapları Listele", callback_data="list_accounts")],
        [InlineKeyboardButton("🔗 Kaynak Grup Ayarla", callback_data="set_source")],
        [InlineKeyboardButton("🎯 Hedef Grup Ayarla", callback_data="set_target")],
        [InlineKeyboardButton("🔍 Tarama Başlat", callback_data="scan_start")],
        [InlineKeyboardButton("➕ Üye Eklemeyi Başlat", callback_data="start_adding")],
        [InlineKeyboardButton("📊 Durum", callback_data="status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "🤖 <b>Telegram Üye Çekme Botu</b>\n\n"
        f"📱 Kayıtlı Hesap: {len(state.sessions)}\n"
        f"🔗 Kaynak Grup: {state.config.get('source_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
        f"🎯 Hedef Grup: {state.config.get('target_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
        f"👥 Taranan Kullanıcı: {len(state.config.get('scanned_users', []))}\n"
        f"✅ Toplam Eklenen: {len(state.added_users)}\n"
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

    elif data == "list_accounts":
        if not state.sessions:
            await query.message.edit_text("📋 Kayıtlı hesap yok.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        text = "📋 <b>Kayıtlı Hesaplar:</b>\n\n"
        for i, s in enumerate(state.sessions, 1):
            text += f"{i}. {s['phone']}\n"
        text += "\n/start ile menüye dön."
        await query.message.edit_text(text, parse_mode="HTML")
        return ConversationHandler.END

    elif data == "set_source":
        await query.message.edit_text(
            "🔗 <b>Kaynak Grup Ayarlama</b>\n\n"
            "Üyelerin çekileceği grubun linkini girin:\n"
            "Örnek: https://t.me/grupadi veya @grupadi\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_SOURCE

    elif data == "set_target":
        await query.message.edit_text(
            "🎯 <b>Hedef Grup Ayarlama</b>\n\n"
            "Üyelerin ekleneceği grubun linkini girin:\n"
            "Örnek: https://t.me/grupadi veya @grupadi\n\n"
            "/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_TARGET

    elif data == "scan_start":
        if not state.config.get("source_group"):
            await query.message.edit_text("❌ Önce kaynak grup ayarlayın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.sessions:
            await query.message.edit_text("❌ Önce en az bir hesap ekleyin.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton("👥 Tüm Üyeleri Çek", callback_data="scan_members")],
            [InlineKeyboardButton("💬 Mesaj Atanları Çek", callback_data="scan_messages")],
            [InlineKeyboardButton("🔙 Geri", callback_data="back_menu")],
        ]
        await query.message.edit_text(
            "🔍 <b>Tarama Türü Seçin:</b>\n\n"
            "👥 <b>Tüm Üyeleri Çek:</b> Gruptaki tüm üyeleri tarar\n"
            "💬 <b>Mesaj Atanları Çek:</b> Grupta mesaj atan benzersiz kullanıcıları tarar",
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

    elif data == "start_adding":
        if not state.config.get("scanned_users"):
            await query.message.edit_text("❌ Önce tarama yapın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.config.get("target_group"):
            await query.message.edit_text("❌ Önce hedef grup ayarlayın.\n\n/start ile menüye dön.")
            return ConversationHandler.END
        if not state.sessions:
            await query.message.edit_text("❌ Önce en az bir hesap ekleyin.\n\n/start ile menüye dön.")
            return ConversationHandler.END

        available = [u for u in state.config["scanned_users"] if u not in state.added_users]
        await query.message.edit_text(
            f"➕ <b>Üye Ekleme</b>\n\n"
            f"Toplam taranan: {len(state.config['scanned_users'])}\n"
            f"Daha önce eklenen: {len(state.added_users)}\n"
            f"Eklenebilir: {len(available)}\n\n"
            f"Kaç kişi eklemek istiyorsunuz? (Sayı girin)\n\n"
            f"/iptal yazarak iptal edebilirsiniz.",
            parse_mode="HTML"
        )
        return SETTING_ADD_COUNT

    elif data == "status":
        available = [u for u in state.config.get("scanned_users", []) if u not in state.added_users]
        text = (
            "📊 <b>Durum Raporu</b>\n\n"
            f"📱 Kayıtlı Hesap: {len(state.sessions)}\n"
            f"🔗 Kaynak Grup: {state.config.get('source_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
            f"🎯 Hedef Grup: {state.config.get('target_group', 'Ayarlanmadı') or 'Ayarlanmadı'}\n"
            f"👥 Taranan Kullanıcı: {len(state.config.get('scanned_users', []))}\n"
            f"✅ Başarıyla Eklenen: {len(state.added_users)}\n"
            f"⏳ Eklenebilir: {len(available)}\n"
            f"🔄 İşlem Durumu: {'Çalışıyor' if state.is_running else 'Boşta'}\n"
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


async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    state.config["target_group"] = link
    state.save_config()
    await update.message.reply_text(
        f"✅ Hedef grup ayarlandı: <b>{link}</b>\n\n/start ile menüye dön.",
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def set_add_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Geçerli bir sayı girin:")
        return SETTING_ADD_COUNT

    if state.is_running:
        await update.message.reply_text("⚠️ Zaten bir işlem devam ediyor.\n\n/start ile menüye dön.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"🚀 Üye ekleme başlatılıyor... ({count} kişi)\n"
        "İşlem arka planda devam edecek."
    )
    asyncio.create_task(add_members_task(update.message, count))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ İşlem iptal edildi.\n\n/start ile menüye dön.")
    if state.temp_client:
        await state.temp_client.disconnect()
        state.temp_client = None
    return ConversationHandler.END


async def get_client(session_data):
    """Create and connect a Telethon client from session data."""
    client = TelegramClient(
        StringSession(session_data["session_string"]),
        API_ID, API_HASH
    )
    await client.connect()
    if not await client.is_user_authorized():
        return None
    return client


async def resolve_group(client, group_link):
    """Resolve a group link to entity."""
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
    """Scan group for members or message senders."""
    try:
        session_data = state.sessions[0]
        client = await get_client(session_data)
        if not client:
            await message.edit_text("❌ Hesap oturumu geçersiz. Tekrar ekleyin.\n\n/start ile menüye dön.")
            return

        group = await resolve_group(client, state.config["source_group"])
        users = set()

        if scan_type == "members":
            await message.edit_text("🔍 Tüm üyeler taranıyor...")
            async for user in client.iter_participants(group):
                if user.username and not user.bot:
                    users.add(user.username)
        else:
            await message.edit_text("🔍 Mesaj atanlar taranıyor...")
            async for msg in client.iter_messages(group, limit=None):
                if msg.sender and hasattr(msg.sender, 'username'):
                    if msg.sender.username and not msg.sender.bot:
                        users.add(msg.sender.username)

        state.config["scanned_users"] = list(users)
        state.save_config()

        await client.disconnect()

        already_added = [u for u in users if u in state.added_users]
        available = [u for u in users if u not in state.added_users]

        scan_type_text = "Tüm Üyeler" if scan_type == "members" else "Mesaj Atanlar"
        await message.edit_text(
            f"✅ <b>Tarama Tamamlandı!</b>\n\n"
            f"📋 Tarama Türü: {scan_type_text}\n"
            f"👥 Toplam Benzersiz Kullanıcı: {len(users)}\n"
            f"✅ Daha Önce Eklenmiş: {len(already_added)}\n"
            f"⏳ Eklenebilir: {len(available)}\n\n"
            f"/start ile menüye dön.",
            parse_mode="HTML"
        )

    except Exception as e:
        await message.edit_text(f"❌ Tarama hatası: {str(e)}\n\n/start ile menüye dön.")


async def add_members_task(message, count):
    """Add members to target group using multiple accounts with username."""
    state.is_running = True
    report = {
        "total_requested": count,
        "added": 0,
        "skipped_already_added": 0,
        "errors": 0,
        "banned_accounts": [],
        "added_usernames": [],
        "failed_usernames": [],
        "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    available_users = [u for u in state.config["scanned_users"] if u not in state.added_users]

    if not available_users:
        await message.reply_text("❌ Eklenebilir kullanıcı kalmadı.\n\n/start ile menüye dön.")
        state.is_running = False
        return

    target_count = min(count, len(available_users))
    users_to_add = available_users[:target_count]

    current_account_idx = 0
    client = None

    try:
        # Connect first account
        client = await get_client(state.sessions[current_account_idx])
        if not client:
            await message.reply_text("❌ İlk hesap oturumu geçersiz.\n\n/start ile menüye dön.")
            state.is_running = False
            return

        target_group = await resolve_group(client, state.config["target_group"])

        await message.reply_text(
            f"🚀 <b>Üye ekleme başladı!</b>\n\n"
            f"📱 Aktif Hesap: {state.sessions[current_account_idx]['phone']}\n"
            f"🎯 Hedef: {target_count} kişi\n"
            f"⏱ Tahmini süre: ~{target_count * 45 // 60} dakika",
            parse_mode="HTML"
        )

        for i, username in enumerate(users_to_add):
            if not state.is_running:
                break

            # Check if already added
            if username in state.added_users:
                report["skipped_already_added"] += 1
                continue

            try:
                # Add by username
                user_entity = await client.get_entity(f"@{username}")
                await client(functions.channels.InviteToChannelRequest(
                    channel=target_group,
                    users=[user_entity]
                ))

                state.added_users.append(username)
                state.save_added_users()
                report["added"] += 1
                report["added_usernames"].append(username)

                # Progress update every 10 users
                if report["added"] % 10 == 0:
                    await message.reply_text(
                        f"📊 İlerleme: {report['added']}/{target_count}\n"
                        f"📱 Aktif Hesap: {state.sessions[current_account_idx]['phone']}"
                    )

                # Random delay between adds (30-60 seconds)
                delay = random.randint(30, 60)
                await asyncio.sleep(delay)

            except (errors.UserPrivacyRestrictedError,
                    errors.UserNotMutualContactError,
                    errors.UserChannelsTooMuchError,
                    errors.InputUserDeactivatedError) as e:
                report["errors"] += 1
                report["failed_usernames"].append(f"@{username}: {type(e).__name__}")
                continue

            except errors.FloodWaitError as e:
                await message.reply_text(
                    f"⚠️ Flood hatası! {e.seconds} saniye bekleniyor...\n"
                    f"📱 Hesap: {state.sessions[current_account_idx]['phone']}"
                )
                if e.seconds > 300:
                    # Too long, switch account
                    report["banned_accounts"].append(state.sessions[current_account_idx]['phone'])
                    await client.disconnect()
                    current_account_idx += 1
                    if current_account_idx >= len(state.sessions):
                        await message.reply_text("❌ Tüm hesaplar tükendi!")
                        break
                    await message.reply_text(
                        f"🔄 <b>{state.sessions[current_account_idx]['phone']}</b> hesabından devam ediliyor...",
                        parse_mode="HTML"
                    )
                    client = await get_client(state.sessions[current_account_idx])
                    if client:
                        target_group = await resolve_group(client, state.config["target_group"])
                else:
                    await asyncio.sleep(e.seconds)

            except (errors.PeerFloodError, errors.UserBannedInChannelError,
                    errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError) as e:
                # Account restricted/banned, switch to next
                error_name = type(e).__name__
                report["banned_accounts"].append(state.sessions[current_account_idx]['phone'])

                await message.reply_text(
                    f"⚠️ <b>{state.sessions[current_account_idx]['phone']}</b> hesabı hata aldı! ({error_name})\n",
                    parse_mode="HTML"
                )

                await client.disconnect()
                current_account_idx += 1

                if current_account_idx >= len(state.sessions):
                    await message.reply_text(
                        "❌ Tüm hesaplar tükendi/ban yedi! İşlem durduruluyor."
                    )
                    break

                await message.reply_text(
                    f"🔄 <b>{state.sessions[current_account_idx]['phone']}</b> hesabından devam ediliyor...",
                    parse_mode="HTML"
                )

                client = await get_client(state.sessions[current_account_idx])
                if not client:
                    current_account_idx += 1
                    if current_account_idx >= len(state.sessions):
                        await message.reply_text("❌ Tüm hesaplar tükendi!")
                        break
                    client = await get_client(state.sessions[current_account_idx])

                if client:
                    target_group = await resolve_group(client, state.config["target_group"])

                # Retry current user with new account
                try:
                    user_entity = await client.get_entity(f"@{username}")
                    await client(functions.channels.InviteToChannelRequest(
                        channel=target_group,
                        users=[user_entity]
                    ))
                    state.added_users.append(username)
                    state.save_added_users()
                    report["added"] += 1
                    report["added_usernames"].append(username)
                except:
                    report["errors"] += 1
                    report["failed_usernames"].append(f"@{username}: SwitchRetryFailed")

            except Exception as e:
                error_name = type(e).__name__
                error_str = str(e).lower()

                if "ban" in error_str or "restrict" in error_str or "flood" in error_str or "peer" in error_name.lower():
                    report["banned_accounts"].append(state.sessions[current_account_idx]['phone'])
                    await client.disconnect()

                    current_account_idx += 1
                    if current_account_idx >= len(state.sessions):
                        await message.reply_text(f"❌ Tüm hesaplar tükendi! Son hata: {error_name}")
                        break

                    await message.reply_text(
                        f"⚠️ <b>{state.sessions[current_account_idx - 1]['phone']}</b> ban yedi!\n"
                        f"🔄 <b>{state.sessions[current_account_idx]['phone']}</b> hesabından devam ediliyor...",
                        parse_mode="HTML"
                    )

                    client = await get_client(state.sessions[current_account_idx])
                    if client:
                        target_group = await resolve_group(client, state.config["target_group"])
                else:
                    report["errors"] += 1
                    report["failed_usernames"].append(f"@{username}: {error_name}")

    except Exception as e:
        await message.reply_text(f"❌ Kritik hata: {str(e)}")

    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass
        state.is_running = False

    # Final report
    report["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_text = (
        f"📊 <b>İŞLEM RAPORU</b>\n"
        f"{'═' * 30}\n\n"
        f"⏱ Başlangıç: {report['start_time']}\n"
        f"⏱ Bitiş: {report['end_time']}\n\n"
        f"📋 <b>Özet:</b>\n"
        f"• İstenen: {report['total_requested']} kişi\n"
        f"• ✅ Başarıyla Eklenen: {report['added']} kişi\n"
        f"• ⏭ Atlanan (daha önce eklenmiş): {report['skipped_already_added']} kişi\n"
        f"• ❌ Hatalı: {report['errors']} kişi\n"
        f"• 🚫 Ban/Hata Alan Hesaplar: {len(report['banned_accounts'])}\n\n"
    )

    if report["banned_accounts"]:
        report_text += f"🚫 <b>Ban/Hata Alan Hesaplar:</b>\n"
        for acc in report["banned_accounts"]:
            report_text += f"  • {acc}\n"
        report_text += "\n"

    if report["added_usernames"][:20]:
        report_text += f"✅ <b>Eklenen Kullanıcılar (ilk 20):</b>\n"
        for u in report["added_usernames"][:20]:
            report_text += f"  • @{u}\n"
        if len(report["added_usernames"]) > 20:
            report_text += f"  ... ve {len(report['added_usernames']) - 20} kişi daha\n"
        report_text += "\n"

    if report["failed_usernames"][:10]:
        report_text += f"❌ <b>Başarısız (ilk 10):</b>\n"
        for u in report["failed_usernames"][:10]:
            report_text += f"  • {u}\n"
        report_text += "\n"

    report_text += f"\n/start ile menüye dön."

    await message.reply_text(report_text, parse_mode="HTML")


def run_bot_polling():
    """Run bot with manual async setup to avoid signal handler issues."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(button_handler),
        ],
        states={
            ADDING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phone)],
            ADDING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_code)],
            ADDING_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_2fa)],
            SETTING_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_source)],
            SETTING_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_target)],
            SETTING_ADD_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_add_count)],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv_handler)

    async def start_bot():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print("🤖 Bot başlatıldı!")
        # Keep running
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
    if not ADMIN_ID:
        print("HATA: ADMIN_ID ayarlanmamış!")
        return

    print(f"🤖 Bot başlatılıyor...")
    print(f"📱 Admin ID: {ADMIN_ID}")
    print(f"🔑 API ID: {API_ID}")

    # Start Flask in a separate thread for health check
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Health check server started on port {PORT}")

    # Wait for Flask to bind port
    time.sleep(2)

    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("💓 Keep-alive started (10 min interval)")

    # Run bot in main thread (using manual async, no signal handlers)
    run_bot_polling()


if __name__ == "__main__":
    main()
