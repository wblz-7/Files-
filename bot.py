import html
import json
import logging
import os
import re
import tempfile
import uuid
from asyncio import create_task, sleep
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = "8833127812:AAE8Er5bGBndr3-5XJhU4QdPk3WU4CZmU5Y"
BOT_USERNAME = "WlzbeyFilesBot"
DATA_FILE = "wlzbibot.json"
LOG_FILE = "access.log"
DEFAULT_EXPIRY = 1500
OWNER_ID = 7194867487
FILES_PER_PAGE = 6
CHANNELS_PER_PAGE = 6
BROADCAST_PER_PAGE = 6
MAX_ACTIVITY = 80
MAX_BROADCASTS = 80

(
    WAIT_TEXT,
    GEN_CONTENT,
    CH_REF,
    CH_URL,
    CH_BTN,
    CH_FJ,
    CH_PUB,
    BC_CONTENT,
    BC_BTN_TEXT,
    BC_BTN_URL,
    BC_PREVIEW,
    POST_CHANNELS,
    POST_CH_REF,
    POST_CONTENT,
    POST_BTN_TEXT,
    POST_BTN_URL,
    POST_BTN_PLACE,
    POST_PREVIEW,
) = range(100, 118)

file_store: Dict[str, Dict[str, Any]] = {}
registered_users = set()
channels: List[Dict[str, Any]] = []
broadcasts: List[Dict[str, Any]] = []
activity: List[Dict[str, Any]] = []
file_access_count: Dict[str, int] = {}
migrated_note = "No migration required."

settings: Dict[str, Any] = {
    "expiry_seconds": DEFAULT_EXPIRY,
    "welcome_message": (
        "Hey, welcome to the bot.\n"
        "You can use it to receive files securely.\n"
        "Files you receive are automatically deleted after {expiry} minutes.\n"
        "Bot by @rejrk"
    ),
    "deletion_message": "File deleted. Access again:",
    "expired_file_message": "This file is no longer available.",
    "channel_link_after_delete": "https://t.me/+w6_Kos9AuPo4N2I1",
    "join_button_text": "Join",
    "force_join_enabled": True,
    "auto_delete": True,
    "access_again": True,
    "register_on_start": True,
    "admins": [OWNER_ID],
    "owner_id": OWNER_ID,
    "user_links": [
        {"text": "Main Channel", "url": "https://t.me/wblzy"},
        {"text": "Group Chat", "url": "https://t.me/parithings"},
        {"text": "Backup Channel", "url": "https://t.me/wblzey"},
    ],
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("wblzy")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def fmt_dt(value: Optional[str]) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def owner_id() -> int:
    try:
        return int(settings.get("owner_id", OWNER_ID))
    except (TypeError, ValueError):
        return OWNER_ID


def admin_ids() -> List[int]:
    out = []
    for item in settings.get("admins", []):
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    if owner_id() not in out:
        out.insert(0, owner_id())
        settings["admins"] = out
    return out


def is_admin(user_id: Optional[int]) -> bool:
    return user_id is not None and int(user_id) in admin_ids()


def is_owner(user_id: Optional[int]) -> bool:
    return user_id is not None and int(user_id) == owner_id()


def bot_name(context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
    if context is not None:
        name = getattr(context.bot, "username", None)
        if name:
            return name
    return BOT_USERNAME


def file_url(file_id: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
    return f"https://t.me/{bot_name(context)}?start={file_id}"


def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def b(text: str, data: Optional[str] = None, url: Optional[str] = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=data or "noop")


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def reset_defaults() -> None:
    global file_store, registered_users, channels, broadcasts, activity, file_access_count
    file_store = {}
    registered_users = set()
    channels = []
    broadcasts = []
    activity = []
    file_access_count = {}
    settings.clear()
    settings.update({
        "expiry_seconds": DEFAULT_EXPIRY,
        "welcome_message": (
            "Hey, welcome to the bot.\n"
            "You can use it to receive files securely.\n"
            "Files you receive are automatically deleted after {expiry} minutes.\n"
            "Bot by @Wlzbi"
        ),
        "deletion_message": "File deleted. Access again:",
        "expired_file_message": "This file is no longer available.",
        "channel_link_after_delete": "https://t.me/+w6_Kos9AuPo4N2I1",
        "join_button_text": "Join",
        "force_join_enabled": True,
        "auto_delete": True,
        "access_again": True,
        "register_on_start": True,
        "admins": [OWNER_ID],
        "owner_id": OWNER_ID,
        "user_links": [
            {"text": "Main Channel", "url": "https://t.me/+w6_Kos9AuPo4N2I1"},
            {"text": "Group Chat", "url": "https://t.me/parithings"},
            {"text": "Backup Channel", "url": "https://t.me/backupwlzbi"},
        ],
    })


def channel_key(ch: Dict[str, Any]) -> str:
    return str(ch.get("id", ""))


def find_channel(cid: Any) -> Optional[Dict[str, Any]]:
    target = str(cid)
    for ch in channels:
        if str(ch.get("id")) == target:
            return ch
    return None


def forcejoin_list() -> List[Dict[str, Any]]:
    if not settings.get("force_join_enabled", True):
        return []
    return [ch for ch in channels if ch.get("force_join") and ch.get("status", "active") == "active"]


def publishing_list() -> List[Dict[str, Any]]:
    return [ch for ch in channels if ch.get("publishing") and ch.get("status", "active") == "active"]


def migrate_loaded(data: Dict[str, Any]) -> str:
    global file_store, registered_users, channels, broadcasts, activity, file_access_count
    notes = []
    file_store = data.get("file_store", {}) or {}
    registered_users = set(data.get("registered_users", []) or [])
    broadcasts = data.get("broadcasts", []) or []
    activity = data.get("activity", []) or []
    file_access_count = data.get("file_access_count", {}) or {}
    loaded_settings = data.get("settings", {}) or {}
    settings.update(loaded_settings)
    settings["owner_id"] = int(settings.get("owner_id", OWNER_ID) or OWNER_ID)
    raw_admins = settings.get("admins", [OWNER_ID])
    clean_admins = []
    for item in raw_admins:
        try:
            clean_admins.append(int(item))
        except (TypeError, ValueError):
            continue
    if settings["owner_id"] not in clean_admins:
        clean_admins.insert(0, settings["owner_id"])
        notes.append("owner restored")
    settings["admins"] = clean_admins
    settings.setdefault("expired_file_message", "This file is no longer available.")
    settings.setdefault("join_button_text", "Join")
    settings.setdefault("force_join_enabled", True)
    settings.setdefault("auto_delete", True)
    settings.setdefault("access_again", True)
    settings.setdefault("register_on_start", True)
    settings.setdefault("user_links", [
        {"text": "Main Channel", "url": "https://t.me/+w6_Kos9AuPo4N2I1"},
        {"text": "Group Chat", "url": "https://t.me/parithings"},
        {"text": "Backup Channel", "url": "https://t.me/backupwlzbi"},
    ])

    loaded_channels = data.get("channels")
    if loaded_channels:
        channels = loaded_channels
        notes.append("channels schema present")
    else:
        channels = []
        old_fj = data.get("forcejoin_channels", []) or []
        for ch in old_fj:
            channels.append({
                "id": ch.get("id"),
                "title": ch.get("title") or ch.get("btn_text") or str(ch.get("id")),
                "username": ch.get("username", ""),
                "join_url": ch.get("url") or ch.get("join_url") or "",
                "force_join": True,
                "publishing": False,
                "btn_text": ch.get("btn_text") or ch.get("title") or "Join",
                "status": "active",
            })
        old_pub = data.get("saved_post_channels", []) or []
        for raw in old_pub:
            existing = find_channel(raw)
            if existing:
                existing["publishing"] = True
            else:
                channels.append({
                    "id": raw,
                    "title": str(raw),
                    "username": "",
                    "join_url": "",
                    "force_join": False,
                    "publishing": True,
                    "btn_text": settings.get("join_button_text", "Join"),
                    "status": "active",
                })
        if old_fj or old_pub:
            notes.append("migrated force-join and publishing channels")

    changed_files = False
    for fid, item in list(file_store.items()):
        if not isinstance(item, dict):
            continue
        item.setdefault("type", "document")
        item.setdefault("caption", "")
        item.setdefault("expiry_seconds", settings.get("expiry_seconds", DEFAULT_EXPIRY))
        item.setdefault("created_at", iso_now())
        item.setdefault("status", "active")
        if "access_count" not in item:
            item["access_count"] = int(file_access_count.get(fid, 0) or 0)
            changed_files = True
        file_access_count[fid] = int(item.get("access_count", 0) or 0)
    if changed_files:
        notes.append("migrated file access counts")
    return ", ".join(notes) if notes else "Existing database loaded."


def derived_forcejoin() -> List[Dict[str, Any]]:
    out = []
    for ch in channels:
        if ch.get("force_join"):
            out.append({
                "id": ch.get("id"),
                "title": ch.get("title"),
                "btn_text": ch.get("btn_text"),
                "url": ch.get("join_url"),
            })
    return out


def derived_publishing() -> List[str]:
    return [str(ch.get("id")) for ch in channels if ch.get("publishing")]


def save_data() -> None:
    payload = {
        "file_store": file_store,
        "registered_users": list(registered_users),
        "channels": channels,
        "forcejoin_channels": derived_forcejoin(),
        "saved_post_channels": derived_publishing(),
        "settings": settings,
        "file_access_count": {fid: int((item or {}).get("access_count", file_access_count.get(fid, 0)) or 0) for fid, item in file_store.items()},
        "broadcasts": broadcasts[-MAX_BROADCASTS:],
        "activity": activity[-MAX_ACTIVITY:],
        "schema_version": 2,
    }
    directory = os.path.dirname(os.path.abspath(DATA_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="wlzbi_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DATA_FILE)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_data() -> None:
    global migrated_note
    if not os.path.exists(DATA_FILE):
        reset_defaults()
        save_data()
        migrated_note = "New database created."
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        migrated_note = migrate_loaded(data)
        save_data()
    except Exception as exc:
        logger.error("Failed to load data: %s", exc)
        reset_defaults()
        migrated_note = "Database could not be read; defaults restored."


load_data()


def log_activity(kind: str, detail: str, user_id: Optional[int] = None) -> None:
    activity.append({
        "ts": iso_now(),
        "kind": kind,
        "detail": detail,
        "user_id": user_id,
    })
    del activity[:-MAX_ACTIVITY]
    save_data()


def log_access(user_id: int, file_id: str) -> None:
    line = f"{utc_now()} | User {user_id} accessed file {file_id}"
    logger.info(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.error("Failed to write access log: %s", exc)
    item = file_store.get(file_id)
    if item is not None:
        item["access_count"] = int(item.get("access_count", 0) or 0) + 1
        file_access_count[file_id] = item["access_count"]
    else:
        file_access_count[file_id] = int(file_access_count.get(file_id, 0) or 0) + 1
    save_data()


def total_accesses() -> int:
    return sum(int((item or {}).get("access_count", 0) or 0) for item in file_store.values())


def expiry_minutes() -> int:
    return max(1, int(settings.get("expiry_seconds", DEFAULT_EXPIRY)) // 60)


def parse_positive_minutes(text: str) -> Optional[int]:
    raw = (text or "").strip()
    if not raw or not re.fullmatch(r"[0-9]+", raw):
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0 or value > 525600:
        return None
    return value


def valid_http_url(text: str) -> bool:
    value = (text or "").strip()
    return bool(re.match(r"^(https?://|tg://)\S+$", value, re.IGNORECASE))


def message_kind(message: Message) -> str:
    if message.document:
        return "document"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.animation:
        return "animation"
    if message.sticker:
        return "sticker"
    if message.video_note:
        return "video_note"
    if message.text:
        return "text"
    return "message"


def media_file_id(message: Message) -> Optional[str]:
    if message.document:
        return message.document.file_id
    if message.photo:
        return message.photo[-1].file_id
    if message.video:
        return message.video.file_id
    if message.audio:
        return message.audio.file_id
    if message.voice:
        return message.voice.file_id
    if message.animation:
        return message.animation.file_id
    if message.sticker:
        return message.sticker.file_id
    if message.video_note:
        return message.video_note.file_id
    return None


def parse_chat_ref(text: str) -> Any:
    value = (text or "").strip()
    if not value:
        return None
    invite = re.search(r"(https?://t\.me/\+\S+|https?://t\.me/joinchat/\S+)", value, re.IGNORECASE)
    if value.lstrip("-").isdigit():
        return int(value)
    if value.startswith("@"):
        return value
    public = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/?", value, re.IGNORECASE)
    if public and public.group(1).lower() not in {"joinchat", "c", "addstickers"}:
        return f"@{public.group(1)}"
    private = re.search(r"t\.me/c/(\d+)", value, re.IGNORECASE)
    if private:
        return int(f"-100{private.group(1)}")
    if invite:
        return value
    return value


def group_url_buttons(items: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    grouped: Dict[int, List[InlineKeyboardButton]] = {}
    for item in items:
        row = int(item.get("row", 0) or 0)
        grouped.setdefault(row, []).append(InlineKeyboardButton(item["text"], url=item["url"]))
    rows = [grouped[key] for key in sorted(grouped)]
    return kb(rows) if rows else kb([])


def clear_conv(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "wait_kind",
        "wait_return",
        "wait_target",
        "file_search",
        "ch_add",
        "bc",
        "post",
        "nav_parent",
    ):
        context.user_data.pop(key, None)


async def answer(query) -> None:
    if not query:
        return
    try:
        await query.answer()
    except TelegramError:
        pass


async def deny(update: Update) -> None:
    query = update.callback_query
    if query:
        try:
            await query.answer("Not authorized.", show_alert=True)
        except TelegramError:
            pass
        return
    if update.effective_message:
        await update.effective_message.reply_text("This action is limited to administrators.")


async def present(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    query = update.callback_query
    if query:
        await answer(query)
        try:
            await query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except BadRequest as exc:
            msg = str(exc).lower()
            if "not modified" in msg:
                return
        try:
            await query.message.reply_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except TelegramError:
            logger.exception("Failed to present callback message")
            return
    message = update.effective_message
    if message:
        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def dash_pair(left: str, right: str) -> str:
    pad = max(4, 24 - len(left))
    return left + (" " * pad) + right


def dashboard_text() -> str:
    users = len(registered_users)
    files = len(file_store)
    accesses = total_accesses()
    ch_count = len(channels)
    return (
        "🤖 WLZBI Manager Bot\n\n"
        f"{dash_pair(f'👥 {users} Users', f'📁 {files} Files')}\n"
        f"{dash_pair(f'🔗 {accesses} Accesses', f'📢 {ch_count} Channels')}\n\n"
        "Choose an option below."
    )


def dashboard_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("📁 Files", "nav:files")],
        [b("📢 Broadcast", "nav:broadcast")],
        [b("📝 Posts", "nav:posts")],
        [b("📺 Channels", "nav:channels")],
        [b("📊 Analytics", "nav:analytics")],
        [b("⚙️ Settings", "nav:settings")],
    ])


def files_home_text() -> str:
    return (
        "📁 Files\n\n"
        f"{len(file_store)} stored in database.\n"
        "Generate links, search, or open an existing file."
    )


def files_home_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("🔗 Generate Link", "file:gen")],
        [b("📋 List", "file:list:0"), b("🔎 Search", "in:fsearch")],
        [b("⬅️ Back", "nav:main")],
    ])


def settings_home_text() -> str:
    return (
        "⚙️ Settings\n\n"
        "Configure bot behavior, messages, files, channels, administrators and advanced options."
    )


def settings_home_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("⚙️ General", "set:general")],
        [b("💬 Messages", "set:messages")],
        [b("📁 Files", "set:files")],
        [b("📺 Channels", "set:channels")],
        [b("👥 Administrators", "set:admins")],
        [b("🛠 Advanced", "set:advanced")],
        [b("⬅️ Back", "nav:main")],
    ])


def on_off(value: bool) -> str:
    return "On" if value else "Off"


async def check_user_channels(bot, user_id: int) -> List[Dict[str, Any]]:
    missing = []
    for ch in forcejoin_list():
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            status = getattr(member, "status", "")
            is_member = bool(getattr(member, "is_member", False))
            if status in ("member", "administrator", "creator") or (status == "restricted" and is_member):
                continue
            missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing


def join_markup(missing: List[Dict[str, Any]], file_id: str) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        label = ch.get("btn_text") or settings.get("join_button_text") or ch.get("title") or "Join"
        url = ch.get("join_url") or ""
        if url:
            rows.append([b(str(label), url=url)])
    rows.append([b("✅ Check", f"join:ok:{file_id}")])
    return kb(rows)


async def send_legacy_file(bot, user_id: int, item: Dict[str, Any]) -> Optional[Message]:
    kind = item.get("type")
    file_id = item.get("file_id")
    caption = item.get("caption") or ""
    if kind == "document" and file_id:
        return await bot.send_document(chat_id=user_id, document=file_id, caption=caption)
    if kind == "photo" and file_id:
        return await bot.send_photo(chat_id=user_id, photo=file_id, caption=caption)
    if kind == "video" and file_id:
        return await bot.send_video(chat_id=user_id, video=file_id, caption=caption)
    if kind == "audio" and file_id:
        return await bot.send_audio(chat_id=user_id, audio=file_id, caption=caption)
    if kind == "voice" and file_id:
        return await bot.send_voice(chat_id=user_id, voice=file_id, caption=caption)
    if kind == "animation" and file_id:
        return await bot.send_animation(chat_id=user_id, animation=file_id, caption=caption)
    if kind == "sticker" and file_id:
        return await bot.send_sticker(chat_id=user_id, sticker=file_id)
    if item.get("text"):
        return await bot.send_message(chat_id=user_id, text=item.get("text"))
    if caption:
        return await bot.send_message(chat_id=user_id, text=caption)
    return None


async def deliver_content(bot, user_id: int, item: Dict[str, Any]) -> Optional[Message]:
    src_chat = item.get("source_chat_id")
    src_msg = item.get("source_message_id")
    if src_chat and src_msg:
        try:
            return await bot.copy_message(chat_id=user_id, from_chat_id=src_chat, message_id=src_msg)
        except TelegramError as exc:
            logger.warning("copy_message failed, using fallback: %s", exc)
    return await send_legacy_file(bot, user_id, item)


async def send_file_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, file_id: str) -> None:
    item = file_store.get(file_id)
    if not item or item.get("status") != "active":
        markup = kb([[b("Contact 📞", url=settings.get("channel_link_after_delete"))]]) if settings.get("channel_link_after_delete") else None
        await context.bot.send_message(
            chat_id=user_id,
            text=settings.get("expired_file_message") or "This file is no longer available.",
            reply_markup=markup,
        )
        return
    log_access(user_id, file_id)
    try:
        msg = await deliver_content(context.bot, user_id, item)
    except Forbidden:
        logger.info("User %s blocked the bot during file delivery", user_id)
        return
    except TelegramError as exc:
        logger.error("Failed to send file %s to %s: %s", file_id, user_id, exc)
        try:
            await context.bot.send_message(chat_id=user_id, text="The file could not be delivered right now.")
        except TelegramError:
            pass
        return
    expiry = int(item.get("expiry_seconds", settings.get("expiry_seconds", DEFAULT_EXPIRY)) or DEFAULT_EXPIRY)
    warn = None
    if settings.get("auto_delete", True):
        try:
            minutes = max(1, expiry // 60)
            warn = await context.bot.send_message(
                chat_id=user_id,
                text=f"This file will be deleted in {minutes} minutes.",
            )
        except TelegramError:
            warn = None

    async def delete_later() -> None:
        await sleep(expiry)
        if not settings.get("auto_delete", True):
            return
        try:
            if msg:
                await context.bot.delete_message(chat_id=user_id, message_id=msg.message_id)
        except TelegramError:
            pass
        try:
            if warn:
                await context.bot.delete_message(chat_id=user_id, message_id=warn.message_id)
        except TelegramError:
            pass
        extra = None
        if settings.get("access_again", True) and file_id in file_store:
            extra = kb([[b("Access again", url=file_url(file_id, context))]])
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=settings.get("deletion_message") or "File deleted.",
                reply_markup=extra,
            )
        except TelegramError:
            pass

    if settings.get("auto_delete", True):
        create_task(delete_later())


def welcome_markup() -> Optional[InlineKeyboardMarkup]:
    rows = []
    for item in settings.get("user_links", []) or []:
        if item.get("text") and item.get("url"):
            rows.append([b(str(item["text"]), url=str(item["url"]))])
    return kb(rows) if rows else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    if settings.get("register_on_start", True) or context.args:
        registered_users.add(user.id)
        save_data()
    if context.args:
        link_id = context.args[0]
        missing = await check_user_channels(context.bot, user.id)
        if missing:
            await update.effective_message.reply_text(
                "Join the required channels to continue.",
                reply_markup=join_markup(missing, link_id),
            )
            return ConversationHandler.END
        await send_file_to_user(context, user.id, link_id)
        return ConversationHandler.END
    if not is_admin(user.id):
        welcome = settings.get("welcome_message", "Welcome.").replace("{expiry}", str(expiry_minutes()))
        try:
            welcome = welcome.format(expiry=expiry_minutes())
        except Exception:
            pass
        await update.effective_message.reply_text(welcome, reply_markup=welcome_markup())
        return ConversationHandler.END
    await present(update, dashboard_text(), dashboard_markup())
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and is_admin(user.id):
        text = (
            "WLZBI Manager Bot\n\n"
            "/start — Open the dashboard\n"
            "/help — Show this help\n"
            "/cancel — Cancel the current operation\n\n"
            "Bot by @rejerks | WLZBI"
        )
    else:
        text = (
            "This bot delivers files with optional channel checks and automatic deletion.\n"
            "Open a generated link to receive content.\n"
            "For support, contact @Wlzbi."
        )
    await update.effective_message.reply_text(text)


async def global_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parent = context.user_data.get("nav_parent", "nav:main")
    clear_conv(context)
    if update.effective_message:
        await update.effective_message.reply_text("Operation cancelled.")
    dummy = update
    if is_admin(update.effective_user.id if update.effective_user else None):
        await show_by_key(dummy, context, parent, as_reply=True)
    return ConversationHandler.END


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data if query else "nav:main"
    clear_conv(context)
    if not is_admin(query.from_user.id if query else None):
        await deny(update)
        return ConversationHandler.END
    await route_admin(update, context, data)
    return ConversationHandler.END


async def joined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await answer(query)
    user_id = query.from_user.id
    parts = (query.data or "").split(":", 2)
    link_id = parts[2] if len(parts) > 2 else ""
    registered_users.add(user_id)
    save_data()
    missing = await check_user_channels(context.bot, user_id)
    if not missing:
        try:
            await query.edit_message_text("Required channels verified.")
        except TelegramError:
            pass
        if link_id:
            await send_file_to_user(context, user_id, link_id)
        return
    try:
        await query.edit_message_text(
            "Join the required channels to continue.",
            reply_markup=join_markup(missing, link_id),
        )
    except TelegramError:
        pass


def file_label(fid: str, item: Dict[str, Any]) -> str:
    caption = (item.get("caption") or item.get("text") or "").strip()
    kind = item.get("type") or "file"
    title = caption[:22] if caption else kind
    return f"{fid} · {title}"


def file_detail_text(fid: str, item: Dict[str, Any], context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
    expiry = int(item.get("expiry_seconds", settings.get("expiry_seconds", DEFAULT_EXPIRY)) or DEFAULT_EXPIRY)
    status = item.get("status", "active")
    return (
        "📁 File Details\n\n"
        f"ID: <code>{esc(fid)}</code>\n"
        f"Type: {esc(item.get('type') or 'unknown')}\n"
        f"Created: {esc(fmt_dt(item.get('created_at')))}\n"
        f"Expiry: {expiry // 60} minutes\n"
        f"Accesses: {int(item.get('access_count', 0) or 0)}\n"
        f"Status: {esc(status)}\n"
        f"Link: {esc(file_url(fid, context))}"
    )


def file_detail_markup(fid: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> InlineKeyboardMarkup:
    return kb([
        [b("✏️ Edit", f"in:fcap:{fid}"), b("⏳ Expiry", f"in:fexp:{fid}")],
        [b("🔗 Open Link", url=file_url(fid, context))],
        [b("🗑 Delete", f"file:rm:{fid}")],
        [b("⬅️ Back", "file:list:0")],
    ])


def paginate(items: List[Any], page: int, per_page: int) -> Tuple[List[Any], int, int]:
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = max(0, min(page, pages - 1))
    start = page * per_page
    return items[start:start + per_page], page, pages


def file_items(search: Optional[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
    items = list(file_store.items())
    items.sort(key=lambda pair: str(pair[1].get("created_at", "")), reverse=True)
    if search:
        needle = search.lower()
        items = [
            (fid, item)
            for fid, item in items
            if needle in fid.lower()
            or needle in str(item.get("type", "")).lower()
            or needle in str(item.get("caption", "")).lower()
            or needle in str(item.get("text", "")).lower()
        ]
    return items


async def show_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    search = context.user_data.get("file_search")
    items = file_items(search)
    if not items:
        text = "No files found in database." if not search else "No files found in database for that search."
        await present(update, text, kb([
            [b("🔗 Generate Link", "file:gen"), b("🔎 Search", "in:fsearch")],
            [b("⬅️ Back", "nav:files")],
        ]))
        return
    chunk, page, pages = paginate(items, page, FILES_PER_PAGE)
    rows = [[b(file_label(fid, item), f"file:d:{fid}")] for fid, item in chunk]
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(b("⬅️ Previous", f"file:list:{page - 1}"))
    if page < pages - 1:
        nav.append(b("➡️ Next", f"file:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([b("🔎 Search", "in:fsearch"), b("⬅️ Back", "nav:files")])
    header = "📁 Files"
    if search:
        header += f" · “{esc(search)}”"
    start = page * FILES_PER_PAGE + 1
    end = start + len(chunk) - 1
    await present(update, f"{header}\n\n{start}–{end} of {len(items)}", kb(rows))


async def show_file_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, fid: str) -> None:
    item = file_store.get(fid)
    if not item:
        await present(update, "No files found in database.", kb([[b("⬅️ Back", "file:list:0")]]))
        return
    await present(update, file_detail_text(fid, item, context), file_detail_markup(fid, context))


async def gen_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await deny(update)
        return ConversationHandler.END
    context.user_data["nav_parent"] = "nav:files"
    await present(update, "Send the Telegram content to store. Text, media and other copyable messages are supported.\n\n/cancel to stop.")
    return GEN_CONTENT


async def gen_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id if update.effective_user else None):
        return ConversationHandler.END
    message = update.effective_message
    if not message:
        await present(update, "Send a copyable Telegram message.")
        return GEN_CONTENT
    fid = str(uuid.uuid4())[:8]
    while fid in file_store:
        fid = str(uuid.uuid4())[:8]
    file_store[fid] = {
        "type": message_kind(message),
        "file_id": media_file_id(message),
        "caption": message.caption or "",
        "text": message.text or "",
        "source_chat_id": message.chat_id,
        "source_message_id": message.message_id,
        "created_at": iso_now(),
        "expiry_seconds": int(settings.get("expiry_seconds", DEFAULT_EXPIRY)),
        "status": "active",
        "access_count": 0,
    }
    file_access_count[fid] = 0
    save_data()
    log_activity("file_create", fid, update.effective_user.id)
    logger.info("Admin %s generated file %s", update.effective_user.id, fid)
    minutes = int(file_store[fid]["expiry_seconds"]) // 60
    await message.reply_text(
        f"File stored.\nID: {fid}\nLink: {file_url(fid, context)}\nAuto-delete after access: {minutes} minutes.",
        reply_markup=kb([
            [b("📁 File Details", f"file:d:{fid}")],
            [b("⬅️ Files", "nav:files")],
        ]),
    )
    clear_conv(context)
    return ConversationHandler.END


def channel_label(ch: Dict[str, Any]) -> str:
    title = ch.get("title") or ch.get("username") or ch.get("id")
    flags = []
    if ch.get("force_join"):
        flags.append("FJ")
    if ch.get("publishing"):
        flags.append("Pub")
    suffix = f" · {', '.join(flags)}" if flags else ""
    return f"{title}{suffix}"


def channel_detail_text(ch: Dict[str, Any]) -> str:
    username = ch.get("username") or "—"
    return (
        "📺 Channel\n\n"
        f"Title: {esc(ch.get('title') or '—')}\n"
        f"ID: <code>{esc(ch.get('id'))}</code>\n"
        f"Username: {esc(username)}\n"
        f"Join URL: {esc(ch.get('join_url') or '—')}\n"
        f"Status: {esc(ch.get('status') or 'active')}\n"
        f"Force Join: {on_off(bool(ch.get('force_join')))}\n"
        f"Publishing: {on_off(bool(ch.get('publishing')))}"
    )


def channel_detail_markup(ch: Dict[str, Any]) -> InlineKeyboardMarkup:
    cid = channel_key(ch)
    fj = "🔐 Force Join: On" if ch.get("force_join") else "🔐 Force Join: Off"
    pub = "📝 Publishing: On" if ch.get("publishing") else "📝 Publishing: Off"
    return kb([
        [b("✏️ Join text", f"in:cbtn:{cid}"), b("🔗 Join URL", f"in:curl:{cid}")],
        [b(fj, f"ch:fj:{cid}"), b(pub, f"ch:pub:{cid}")],
        [b("🗑 Remove", f"ch:rm:{cid}")],
        [b("⬅️ Back", "ch:list:0")],
    ])


def channels_home_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("➕ Add Channel", "ch:add"), b("📋 Manage Channels", "ch:list:0")],
        [b("🔐 Force Join", "ch:fjlist"), b("📝 Publishing", "ch:publist")],
        [b("⬅️ Back", "nav:main")],
    ])


async def show_channel_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, mode: str = "all") -> None:
    if mode == "fj":
        items = [ch for ch in channels if ch.get("force_join")]
        title = "🔐 Force Join"
        back = "nav:channels"
    elif mode == "pub":
        items = [ch for ch in channels if ch.get("publishing")]
        title = "📝 Publishing Channels"
        back = "nav:channels"
    else:
        items = list(channels)
        title = "📺 Channels"
        back = "nav:channels"
    if not items:
        await present(update, "No channels found in database.", kb([
            [b("➕ Add Channel", "ch:add")],
            [b("⬅️ Back", back)],
        ]))
        return
    chunk, page, pages = paginate(items, page, CHANNELS_PER_PAGE)
    prefix = "ch:list" if mode == "all" else ("ch:fjpage" if mode == "fj" else "ch:pubpage")
    rows = [[b(channel_label(ch), f"ch:d:{channel_key(ch)}")] for ch in chunk]
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(b("⬅️ Previous", f"{prefix}:{page - 1}"))
    if page < pages - 1:
        nav.append(b("➡️ Next", f"{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([b("➕ Add", "ch:add"), b("⬅️ Back", back)])
    start = page * CHANNELS_PER_PAGE + 1
    end = start + len(chunk) - 1
    await present(update, f"{title}\n\n{start}–{end} of {len(items)}", kb(rows))


async def show_channel_detail(update: Update, cid: str) -> None:
    ch = find_channel(cid)
    if not ch:
        await present(update, "No channels found in database.", kb([[b("⬅️ Back", "ch:list:0")]]))
        return
    await present(update, channel_detail_text(ch), channel_detail_markup(ch))


async def verify_channel(bot, ref: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        chat = await bot.get_chat(ref)
    except BadRequest as exc:
        return None, f"The channel could not be resolved. {exc}"
    except Forbidden:
        return None, "The bot is blocked or has no access to that chat."
    except TelegramError as exc:
        return None, f"Telegram rejected the lookup. {exc}"
    bot_id = bot.id
    try:
        member = await bot.get_chat_member(chat.id, bot_id)
    except TelegramError as exc:
        return None, (
            "The bot cannot read members in this channel. Add the bot as an administrator "
            f"with membership visibility, then try again. ({exc})"
        )
    status = getattr(member, "status", "")
    if status not in ("administrator", "creator"):
        return None, "The bot must be an administrator of this channel before it can be added."
    username = f"@{chat.username}" if getattr(chat, "username", None) else ""
    join_url = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else ""
    info = {
        "id": chat.id,
        "title": chat.title or username or str(chat.id),
        "username": username,
        "join_url": join_url,
        "status": "active",
    }
    return info, ""


async def ch_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await deny(update)
        return ConversationHandler.END
    context.user_data["nav_parent"] = "nav:channels"
    context.user_data["ch_add"] = {}
    await present(update, "Send the channel ID, @username, or a supported channel link.\n\n/cancel to stop.")
    return CH_REF


async def ch_add_ref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ref = parse_chat_ref(update.effective_message.text or "")
    if ref is None:
        await update.effective_message.reply_text("That channel reference is not valid. Send an ID, @username, or link.")
        return CH_REF
    info, error = await verify_channel(context.bot, ref)
    if error or not info:
        await update.effective_message.reply_text(error or "Channel verification failed.")
        return CH_REF
    if find_channel(info["id"]):
        await update.effective_message.reply_text("That channel is already in the database.", reply_markup=kb([[b("⬅️ Channels", "nav:channels")]]))
        clear_conv(context)
        return ConversationHandler.END
    context.user_data["ch_add"] = info
    if info.get("join_url"):
        await update.effective_message.reply_text(
            f"Resolved: {info['title']}\nID: {info['id']}\n\nPublic join URL detected. Send a custom invite URL or /skip."
        )
    else:
        await update.effective_message.reply_text(
            f"Resolved: {info['title']}\nID: {info['id']}\n\nSend the private join / invite URL."
        )
    return CH_URL


async def ch_add_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = (update.effective_message.text or "").strip()
    info = context.user_data.setdefault("ch_add", {})
    if not valid_http_url(url):
        await update.effective_message.reply_text("Send a valid http(s) join URL.")
        return CH_URL
    info["join_url"] = url
    await update.effective_message.reply_text("Send the Force Join button text for this channel.")
    return CH_BTN


async def ch_add_url_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    info = context.user_data.setdefault("ch_add", {})
    if not info.get("join_url"):
        await update.effective_message.reply_text("A join URL is required for private channels. Send the invite link.")
        return CH_URL
    await update.effective_message.reply_text("Send the Force Join button text for this channel.")
    return CH_BTN


async def ch_add_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Button text cannot be empty.")
        return CH_BTN
    context.user_data.setdefault("ch_add", {})["btn_text"] = text
    await update.effective_message.reply_text(
        "Enable Force Join for this channel?",
        reply_markup=kb([[b("Yes", "ch:newfj:1"), b("No", "ch:newfj:0")]]),
    )
    return CH_FJ


async def ch_add_fj(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await answer(query)
    flag = query.data.endswith(":1")
    context.user_data.setdefault("ch_add", {})["force_join"] = flag
    await present(
        update,
        "Enable this channel for publishing posts?",
        kb([[b("Yes", "ch:newpub:1"), b("No", "ch:newpub:0")]]),
    )
    return CH_PUB


async def ch_add_pub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await answer(query)
    flag = query.data.endswith(":1")
    info = context.user_data.get("ch_add") or {}
    if not info.get("id"):
        await present(update, "Channel setup expired. Start again.", kb([[b("⬅️ Channels", "nav:channels")]]))
        clear_conv(context)
        return ConversationHandler.END
    record = {
        "id": info.get("id"),
        "title": info.get("title") or str(info.get("id")),
        "username": info.get("username") or "",
        "join_url": info.get("join_url") or "",
        "force_join": bool(info.get("force_join")),
        "publishing": flag,
        "btn_text": info.get("btn_text") or settings.get("join_button_text") or "Join",
        "status": "active",
    }
    channels.append(record)
    save_data()
    log_activity("channel_add", str(record["id"]), query.from_user.id)
    logger.info("Admin %s added channel %s", query.from_user.id, record["id"])
    clear_conv(context)
    await present(update, "Channel saved.\n\n" + channel_detail_text(record), channel_detail_markup(record))
    return ConversationHandler.END


async def bc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await deny(update)
        return ConversationHandler.END
    context.user_data["nav_parent"] = "nav:broadcast"
    context.user_data["bc"] = {"buttons": []}
    await present(update, "Send the broadcast content. Text, media and other copyable Telegram messages are supported.\n\n/cancel to stop.")
    return BC_CONTENT


async def bc_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    bc = context.user_data.setdefault("bc", {"buttons": []})
    bc["from_chat_id"] = message.chat_id
    bc["message_id"] = message.message_id
    await message.reply_text(
        "Add inline URL buttons to this broadcast?",
        reply_markup=kb([
            [b("➕ Add button", "bc:addbtn")],
            [b("⏭ Skip to preview", "bc:preview")],
        ]),
    )
    return BC_PREVIEW


async def bc_preview_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await answer(query)
    data = query.data
    bc = context.user_data.setdefault("bc", {"buttons": []})
    if data == "bc:addbtn":
        await present(update, "Send the button text.")
        return BC_BTN_TEXT
    if data == "bc:preview":
        return await bc_show_preview(update, context)
    if data == "bc:go":
        return await bc_execute(update, context)
    if data == "bc:no":
        clear_conv(context)
        await present(update, "Broadcast cancelled.", kb([[b("⬅️ Broadcast", "nav:broadcast")]]))
        return ConversationHandler.END
    return BC_PREVIEW


async def bc_btn_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Button text cannot be empty.")
        return BC_BTN_TEXT
    context.user_data.setdefault("bc", {"buttons": []})["pending_text"] = text
    await update.effective_message.reply_text("Send the URL for this button.")
    return BC_BTN_URL


async def bc_btn_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = (update.effective_message.text or "").strip()
    if not valid_http_url(url):
        await update.effective_message.reply_text("Send a valid http(s) or tg:// URL.")
        return BC_BTN_URL
    bc = context.user_data.setdefault("bc", {"buttons": []})
    buttons = bc.setdefault("buttons", [])
    row = buttons[-1]["row"] if buttons and len([x for x in buttons if x["row"] == buttons[-1]["row"]]) < 2 else (buttons[-1]["row"] + 1 if buttons else 0)
    if buttons and len([x for x in buttons if x["row"] == buttons[-1]["row"]]) == 1:
        row = buttons[-1]["row"]
        if len([x for x in buttons if x["row"] == row]) >= 2:
            row += 1
    if buttons:
        last_row = buttons[-1]["row"]
        count_last = len([x for x in buttons if x["row"] == last_row])
        row = last_row if count_last < 2 else last_row + 1
    else:
        row = 0
    buttons.append({"text": bc.pop("pending_text", "Open"), "url": url, "row": row})
    await update.effective_message.reply_text(
        "Button added.",
        reply_markup=kb([
            [b("➕ Add button", "bc:addbtn")],
            [b("👁 Preview", "bc:preview")],
        ]),
    )
    return BC_PREVIEW


async def bc_show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bc = context.user_data.get("bc") or {}
    chat_id = update.effective_chat.id
    markup = group_url_buttons(bc.get("buttons") or [])
    extra = markup if (bc.get("buttons")) else None
    try:
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=bc.get("from_chat_id"),
            message_id=bc.get("message_id"),
            reply_markup=extra,
        )
    except TelegramError as exc:
        await present(update, f"Preview failed: {esc(exc)}", kb([[b("⬅️ Broadcast", "nav:broadcast")]]))
        clear_conv(context)
        return ConversationHandler.END
    controls = kb([
        [b("✅ Send", "bc:go")],
        [b("❌ Cancel", "bc:no")],
    ])
    if update.callback_query:
        await present(update, f"Preview above. Recipients: {len(registered_users)}", controls)
    else:
        await update.effective_message.reply_text(f"Preview above. Recipients: {len(registered_users)}", reply_markup=controls)
    return BC_PREVIEW


async def copy_with_retry(bot, user_id: int, from_chat: int, message_id: int, markup=None) -> str:
    attempts = 0
    while attempts < 5:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat,
                message_id=message_id,
                reply_markup=markup,
            )
            return "sent"
        except RetryAfter as exc:
            await sleep(float(getattr(exc, "retry_after", 1)) + 0.4)
            attempts += 1
        except Forbidden:
            return "blocked"
        except (TimedOut, NetworkError):
            attempts += 1
            await sleep(1.0)
        except TelegramError:
            return "failed"
    return "failed"


async def bc_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bc = context.user_data.get("bc") or {}
    users = list(registered_users)
    total = len(users)
    sent = failed = blocked = 0
    markup = group_url_buttons(bc.get("buttons") or []) if bc.get("buttons") else None
    await present(update, f"Broadcast started for {total} users.")
    progress_message = update.effective_message
    for index, uid in enumerate(users, start=1):
        result = await copy_with_retry(context.bot, uid, bc.get("from_chat_id"), bc.get("message_id"), markup)
        if result == "sent":
            sent += 1
        elif result == "blocked":
            blocked += 1
        else:
            failed += 1
        if index % 20 == 0:
            try:
                await progress_message.edit_text(f"Broadcast in progress… {index}/{total}")
            except TelegramError:
                pass
        await sleep(0.04)
    record = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": iso_now(),
        "admin_id": update.effective_user.id if update.effective_user else None,
        "total": total,
        "sent": sent,
        "failed": failed,
        "blocked": blocked,
        "status": "complete",
    }
    broadcasts.append(record)
    save_data()
    log_activity("broadcast", f"{sent}/{total}", record["admin_id"])
    logger.info("Broadcast complete sent=%s failed=%s blocked=%s", sent, failed, blocked)
    clear_conv(context)
    text = (
        "📢 Broadcast Complete\n\n"
        f"👥 Total: {total}\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"🚫 Blocked: {blocked}"
    )
    await present(update, text, kb([[b("⬅️ Broadcast", "nav:broadcast")]]))
    return ConversationHandler.END


def broadcast_home_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("📢 New Broadcast", "bc:new")],
        [b("📋 History", "bc:hist:0")],
        [b("⬅️ Back", "nav:main")],
    ])


async def show_broadcast_history(update: Update, page: int) -> None:
    items = list(reversed(broadcasts))
    if not items:
        await present(update, "No broadcasts recorded in database.", kb([[b("⬅️ Back", "nav:broadcast")]]))
        return
    chunk, page, pages = paginate(items, page, BROADCAST_PER_PAGE)
    lines = []
    for item in chunk:
        lines.append(
            f"{fmt_dt(item.get('timestamp'))} · ✅ {item.get('sent', 0)} / {item.get('total', 0)} · "
            f"❌ {item.get('failed', 0)} · 🚫 {item.get('blocked', 0)}"
        )
    rows: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(b("⬅️ Previous", f"bc:hist:{page - 1}"))
    if page < pages - 1:
        nav.append(b("➡️ Next", f"bc:hist:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([b("⬅️ Back", "nav:broadcast")])
    await present(update, "📢 Broadcast History\n\n" + "\n".join(lines), kb(rows))


def selected_set(context: ContextTypes.DEFAULT_TYPE) -> List[str]:
    post = context.user_data.setdefault("post", {})
    selected = post.setdefault("channels", [])
    return selected


def post_channel_markup(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    selected = set(selected_set(context))
    rows = []
    for ch in channels:
        cid = channel_key(ch)
        mark = "✓ " if cid in selected else ""
        rows.append([b(f"{mark}{channel_label(ch)}", f"post:ct:{cid}")])
    rows.append([b("➕ Add channel", "post:addch")])
    rows.append([b("✅ Continue", "post:content")])
    rows.append([b("⬅️ Back", "nav:posts")])
    return kb(rows)


async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await deny(update)
        return ConversationHandler.END
    context.user_data["nav_parent"] = "nav:posts"
    context.user_data["post"] = {"channels": [], "buttons": []}
    await present(
        update,
        "📝 Posts\n\nSelect one or more target channels. Nothing is published until you confirm.",
        post_channel_markup(context),
    )
    return POST_CHANNELS


async def post_channels_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await answer(query)
    data = query.data
    post = context.user_data.setdefault("post", {"channels": [], "buttons": []})
    if data.startswith("post:ct:"):
        cid = data.split(":", 2)[2]
        selected = post.setdefault("channels", [])
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)
        await present(update, "📝 Posts\n\nSelect one or more target channels.", post_channel_markup(context))
        return POST_CHANNELS
    if data == "post:addch":
        await present(update, "Send the channel ID, @username, or link to add as a publishing channel.")
        return POST_CH_REF
    if data == "post:content":
        if not post.get("channels"):
            await query.answer("Select at least one channel.", show_alert=True)
            return POST_CHANNELS
        await present(update, "Send the post content. Copyable Telegram messages are supported.")
        return POST_CONTENT
    if data == "nav:posts":
        clear_conv(context)
        await present(update, "📝 Posts\n\nCompose and publish content to selected channels.", kb([
            [b("📝 New Post", "post:new")],
            [b("⬅️ Back", "nav:main")],
        ]))
        return ConversationHandler.END
    return POST_CHANNELS


async def post_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ref = parse_chat_ref(update.effective_message.text or "")
    info, error = await verify_channel(context.bot, ref)
    if error or not info:
        await update.effective_message.reply_text(error or "Channel verification failed.")
        return POST_CH_REF
    existing = find_channel(info["id"])
    if existing:
        existing["publishing"] = True
        ch = existing
    else:
        ch = {
            "id": info["id"],
            "title": info["title"],
            "username": info.get("username") or "",
            "join_url": info.get("join_url") or "",
            "force_join": False,
            "publishing": True,
            "btn_text": settings.get("join_button_text") or "Join",
            "status": "active",
        }
        channels.append(ch)
    save_data()
    cid = channel_key(ch)
    selected = selected_set(context)
    if cid not in selected:
        selected.append(cid)
    await update.effective_message.reply_text(
        f"Channel {ch.get('title')} added to this post.",
        reply_markup=post_channel_markup(context),
    )
    return POST_CHANNELS


async def post_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    post = context.user_data.setdefault("post", {"channels": [], "buttons": []})
    post["from_chat_id"] = message.chat_id
    post["message_id"] = message.message_id
    await message.reply_text(
        "Configure URL buttons for this post.",
        reply_markup=kb([
            [b("➕ Add button", "post:addbtn")],
            [b("👁 Preview", "post:preview")],
        ]),
    )
    return POST_BTN_PLACE


async def post_btn_place_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await answer(query)
    data = query.data
    post = context.user_data.setdefault("post", {"channels": [], "buttons": []})
    if data == "post:addbtn":
        await present(update, "Send the button text.")
        return POST_BTN_TEXT
    if data.startswith("post:row:"):
        choice = data.split(":")[2]
        pending = post.get("pending") or {}
        buttons = post.setdefault("buttons", [])
        if not pending:
            await present(update, "Send the button text.")
            return POST_BTN_TEXT
        if choice == "same" and buttons:
            row = buttons[-1]["row"]
        else:
            row = (buttons[-1]["row"] + 1) if buttons else 0
        buttons.append({"text": pending.get("text"), "url": pending.get("url"), "row": row})
        post["pending"] = None
        return await post_buttons_menu(update, context)
    if data.startswith("post:brm:"):
        try:
            idx = int(data.split(":")[2])
            buttons = post.get("buttons") or []
            if 0 <= idx < len(buttons):
                buttons.pop(idx)
        except ValueError:
            pass
        return await post_buttons_menu(update, context)
    if data == "post:preview":
        return await post_show_preview(update, context)
    if data == "post:go":
        return await post_publish(update, context)
    if data == "post:edit":
        await present(
            update,
            "Edit this post.",
            kb([
                [b("📺 Channels", "post:edch"), b("➕ Buttons", "post:addbtn")],
                [b("👁 Preview", "post:preview")],
                [b("❌ Cancel", "post:no")],
            ]),
        )
        return POST_BTN_PLACE
    if data == "post:edch":
        await present(update, "Select target channels.", post_channel_markup(context))
        return POST_CHANNELS
    if data == "post:no":
        clear_conv(context)
        await present(update, "Post cancelled.", kb([[b("⬅️ Posts", "nav:posts")]]))
        return ConversationHandler.END
    return POST_BTN_PLACE


async def post_buttons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post = context.user_data.get("post") or {}
    buttons = post.get("buttons") or []
    rows = []
    for idx, item in enumerate(buttons):
        rows.append([b(f"🗑 {item['text']} (row {item['row'] + 1})", f"post:brm:{idx}")])
    rows.append([b("➕ Add button", "post:addbtn")])
    rows.append([b("👁 Preview", "post:preview")])
    await present(update, "Button builder. Buttons are grouped by row.", kb(rows))
    return POST_BTN_PLACE


async def post_btn_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Button text cannot be empty.")
        return POST_BTN_TEXT
    post = context.user_data.setdefault("post", {"channels": [], "buttons": []})
    post["pending"] = {"text": text}
    await update.effective_message.reply_text("Send the URL for this button.")
    return POST_BTN_URL


async def post_btn_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = (update.effective_message.text or "").strip()
    if not valid_http_url(url):
        await update.effective_message.reply_text("Send a valid http(s) or tg:// URL.")
        return POST_BTN_URL
    post = context.user_data.setdefault("post", {"channels": [], "buttons": []})
    pending = post.setdefault("pending", {})
    pending["url"] = url
    await update.effective_message.reply_text(
        "Choose a row for this button.",
        reply_markup=kb([
            [b("Same row", "post:row:same"), b("New row", "post:row:new")],
        ]),
    )
    return POST_BTN_PLACE


async def post_show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post = context.user_data.get("post") or {}
    extra = group_url_buttons(post.get("buttons") or []) if post.get("buttons") else None
    try:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=post.get("from_chat_id"),
            message_id=post.get("message_id"),
            reply_markup=extra,
        )
    except TelegramError as exc:
        await present(update, f"Preview failed: {esc(exc)}", kb([[b("⬅️ Posts", "nav:posts")]]))
        return POST_BTN_PLACE
    targets = ", ".join(esc(find_channel(cid).get("title") if find_channel(cid) else cid) for cid in post.get("channels", []))
    await present(
        update,
        f"Preview above.\nTargets: {targets}",
        kb([
            [b("✅ Publish", "post:go")],
            [b("✏️ Edit", "post:edit"), b("❌ Cancel", "post:no")],
        ]),
    )
    return POST_PREVIEW


async def post_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post = context.user_data.get("post") or {}
    targets = post.get("channels") or []
    extra = group_url_buttons(post.get("buttons") or []) if post.get("buttons") else None
    ok = fail = 0
    lines = []
    for cid in targets:
        ch = find_channel(cid)
        title = (ch or {}).get("title") if ch else cid
        chat_id = (ch or {}).get("id", cid)
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=post.get("from_chat_id"),
                message_id=post.get("message_id"),
                reply_markup=extra,
            )
            ok += 1
            lines.append(f"✅ {title}")
        except RetryAfter as exc:
            await sleep(float(getattr(exc, "retry_after", 1)) + 0.4)
            try:
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=post.get("from_chat_id"),
                    message_id=post.get("message_id"),
                    reply_markup=extra,
                )
                ok += 1
                lines.append(f"✅ {title}")
            except TelegramError as err:
                fail += 1
                lines.append(f"❌ {title} ({err})")
        except TelegramError as exc:
            fail += 1
            lines.append(f"❌ {title} ({exc})")
    log_activity("post", f"{ok} ok / {fail} failed", update.effective_user.id if update.effective_user else None)
    clear_conv(context)
    await present(
        update,
        "📝 Publish results\n\n" + "\n".join(lines),
        kb([[b("⬅️ Posts", "nav:posts")]]),
    )
    return ConversationHandler.END


async def wait_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await deny(update)
        return ConversationHandler.END
    payload = (query.data or "")[3:]
    kind, _, target = payload.partition(":")
    context.user_data["wait_kind"] = kind
    context.user_data["wait_target"] = target
    prompts = {
        "fcap": ("Send the new caption, or /skip to clear it.", "file"),
        "fexp": ("Send the new expiry time in minutes. Use a positive whole number.", "file"),
        "fsearch": ("Send a search term (ID, type, or caption).", "files"),
        "sexp": ("Send the default expiry time in minutes. Use a positive whole number.", "set_general"),
        "swel": ("Send the new welcome message. Use {expiry} for minutes.", "set_messages"),
        "sdel": ("Send the new file deletion message.", "set_messages"),
        "sexpmsg": ("Send the message shown when a file is missing or unavailable.", "set_messages"),
        "slink": ("Send the backup / channel URL shown after deletion when a file is missing.", "set_files"),
        "sjoin": ("Send the default Force Join button text.", "set_channels"),
        "sadm": ("Send the numeric Telegram user ID of the new administrator.", "set_admins"),
        "cbtn": ("Send the new join button text for this channel.", "channel"),
        "curl": ("Send the new join URL for this channel.", "channel"),
    }
    prompt, ret = prompts.get(kind, ("Send the new value.", "nav:settings"))
    context.user_data["wait_return"] = ret
    context.user_data["nav_parent"] = {
        "file": f"file:d:{target}",
        "files": "nav:files",
        "set_general": "set:general",
        "set_messages": "set:messages",
        "set_files": "set:files",
        "set_channels": "set:channels",
        "set_admins": "set:admins",
        "channel": f"ch:d:{target}",
    }.get(ret, "nav:settings")
    await present(update, prompt + "\n\n/cancel to stop.")
    return WAIT_TEXT


async def wait_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kind = context.user_data.get("wait_kind")
    target = context.user_data.get("wait_target")
    if kind == "fcap" and target in file_store:
        file_store[target]["caption"] = ""
        save_data()
        await update.effective_message.reply_text("Caption cleared.")
        await show_file_detail(update, context, target)
        clear_conv(context)
        return ConversationHandler.END
    await update.effective_message.reply_text("Nothing to skip. Send a value or /cancel.")
    return WAIT_TEXT


async def wait_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id if update.effective_user else None):
        return ConversationHandler.END
    kind = context.user_data.get("wait_kind")
    target = context.user_data.get("wait_target")
    text = (update.effective_message.text or "").strip()
    ret = context.user_data.get("nav_parent", "nav:main")

    if kind == "fcap":
        item = file_store.get(target)
        if not item:
            await update.effective_message.reply_text("No files found in database.")
        else:
            item["caption"] = text
            save_data()
            await update.effective_message.reply_text("Caption updated.")
            await show_file_detail(update, context, target)
            clear_conv(context)
            return ConversationHandler.END
    elif kind == "fexp":
        minutes = parse_positive_minutes(text)
        if minutes is None:
            await update.effective_message.reply_text("Enter a positive whole number of minutes. Zero and negative values are not allowed.")
            return WAIT_TEXT
        item = file_store.get(target)
        if not item:
            await update.effective_message.reply_text("No files found in database.")
        else:
            item["expiry_seconds"] = minutes * 60
            save_data()
            await update.effective_message.reply_text(f"Expiry set to {minutes} minutes.")
            await show_file_detail(update, context, target)
            clear_conv(context)
            return ConversationHandler.END
    elif kind == "fsearch":
        context.user_data["file_search"] = text
        await show_file_list(update, context, 0)
        context.user_data.pop("wait_kind", None)
        return ConversationHandler.END
    elif kind == "sexp":
        minutes = parse_positive_minutes(text)
        if minutes is None:
            await update.effective_message.reply_text("Enter a positive whole number of minutes. Zero and negative values are not allowed.")
            return WAIT_TEXT
        settings["expiry_seconds"] = minutes * 60
        save_data()
        await update.effective_message.reply_text(f"Default expiry set to {minutes} minutes.")
    elif kind == "swel":
        settings["welcome_message"] = text
        save_data()
        await update.effective_message.reply_text("Welcome message updated.")
    elif kind == "sdel":
        settings["deletion_message"] = text
        save_data()
        await update.effective_message.reply_text("Deletion message updated.")
    elif kind == "sexpmsg":
        settings["expired_file_message"] = text
        save_data()
        await update.effective_message.reply_text("Expired-file message updated.")
    elif kind == "slink":
        if not valid_http_url(text):
            await update.effective_message.reply_text("Send a valid http(s) URL.")
            return WAIT_TEXT
        settings["channel_link_after_delete"] = text
        save_data()
        await update.effective_message.reply_text("Backup channel link updated.")
    elif kind == "sjoin":
        settings["join_button_text"] = text
        save_data()
        await update.effective_message.reply_text("Default join button text updated.")
    elif kind == "sadm":
        if not re.fullmatch(r"[0-9]+", text):
            await update.effective_message.reply_text("Send a numeric Telegram user ID.")
            return WAIT_TEXT
        uid = int(text)
        admins = admin_ids()
        if uid in admins:
            await update.effective_message.reply_text("That user is already an administrator.")
        else:
            admins.append(uid)
            settings["admins"] = admins
            save_data()
            logger.info("Admin %s added administrator %s", update.effective_user.id, uid)
            await update.effective_message.reply_text(f"Administrator {uid} added.")
    elif kind == "cbtn":
        ch = find_channel(target)
        if not ch:
            await update.effective_message.reply_text("No channels found in database.")
        else:
            ch["btn_text"] = text
            save_data()
            await update.effective_message.reply_text("Join button text updated.")
            await show_channel_detail(update, target)
            clear_conv(context)
            return ConversationHandler.END
    elif kind == "curl":
        if not valid_http_url(text):
            await update.effective_message.reply_text("Send a valid http(s) join URL.")
            return WAIT_TEXT
        ch = find_channel(target)
        if not ch:
            await update.effective_message.reply_text("No channels found in database.")
        else:
            ch["join_url"] = text
            save_data()
            await update.effective_message.reply_text("Join URL updated.")
            await show_channel_detail(update, target)
            clear_conv(context)
            return ConversationHandler.END
    else:
        await update.effective_message.reply_text("This input is no longer active.")
    clear_conv(context)
    await show_by_key(update, context, ret, as_reply=True)
    return ConversationHandler.END


def settings_general_text() -> str:
    return (
        "⚙️ General\n\n"
        f"Default expiry: {expiry_minutes()} minutes\n"
        f"User registration: {on_off(bool(settings.get('register_on_start', True)))}\n"
        f"Force Join enforcement: {on_off(bool(settings.get('force_join_enabled', True)))}\n"
        f"Registered users: {len(registered_users)}"
    )


def settings_general_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("⏳ Default expiry", "in:sexp")],
        [b(f"Registration: {on_off(bool(settings.get('register_on_start', True)))}", "set:tog:register")],
        [b(f"Force Join: {on_off(bool(settings.get('force_join_enabled', True)))}", "set:tog:fj")],
        [b("⬅️ Back", "nav:settings")],
    ])


def settings_messages_text() -> str:
    return (
        "💬 Messages\n\n"
        "Welcome, deletion and expired-file messages shown to users."
    )


def settings_messages_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("Welcome message", "in:swel")],
        [b("File deletion message", "in:sdel")],
        [b("Expired-file message", "in:sexpmsg")],
        [b("⬅️ Back", "nav:settings")],
    ])


def settings_files_text() -> str:
    return (
        "📁 Files\n\n"
        f"Default expiry: {expiry_minutes()} minutes\n"
        f"Auto-delete: {on_off(bool(settings.get('auto_delete', True)))}\n"
        f"Access-again button: {on_off(bool(settings.get('access_again', True)))}"
    )


def settings_files_markup() -> InlineKeyboardMarkup:
    return kb([
        [b("⏳ Default expiry", "in:sexp")],
        [b(f"Auto-delete: {on_off(bool(settings.get('auto_delete', True)))}", "set:tog:autodel")],
        [b(f"Access again: {on_off(bool(settings.get('access_again', True)))}", "set:tog:again")],
        [b("Backup link", "in:slink")],
        [b("⬅️ Back", "nav:settings")],
    ])


def settings_channels_text() -> str:
    return (
        "📺 Channels\n\n"
        f"Stored channels: {len(channels)}\n"
        f"Force Join: {on_off(bool(settings.get('force_join_enabled', True)))}\n"
        f"Default join text: {settings.get('join_button_text') or 'Join'}"
    )


def settings_channels_markup() -> InlineKeyboardMarkup:
    return kb([
        [b(f"Force Join: {on_off(bool(settings.get('force_join_enabled', True)))}", "set:tog:fj")],
        [b("Default join text", "in:sjoin")],
        [b("Open Channel Manager", "nav:channels")],
        [b("⬅️ Back", "nav:settings")],
    ])


def settings_admins_text() -> str:
    lines = ["👥 Administrators\n"]
    own = owner_id()
    for uid in admin_ids():
        tag = " · owner" if uid == own else ""
        lines.append(f"• {uid}{tag}")
    lines.append("\nThe owner cannot be removed.")
    return "\n".join(lines)


def settings_admins_markup(user_id: int) -> InlineKeyboardMarkup:
    rows = [[b("➕ Add administrator", "in:sadm")]]
    removable = [uid for uid in admin_ids() if uid != owner_id() and uid != user_id]
    if removable:
        rows.append([b("➖ Remove administrator", "set:rmlist")])
    rows.append([b("⬅️ Back", "nav:settings")])
    return kb(rows)


def settings_advanced_text() -> str:
    size = 0
    status = "Missing"
    if os.path.exists(DATA_FILE):
        status = "OK"
        try:
            size = os.path.getsize(DATA_FILE)
        except OSError:
            status = "Unreadable"
    return (
        "🛠 Advanced\n\n"
        f"Database file: {DATA_FILE}\n"
        f"Database status: {status}\n"
        f"Database size: {size} bytes\n"
        f"Log file: {LOG_FILE}\n"
        f"Stored files: {len(file_store)}\n"
        f"Channels: {len(channels)}\n"
        f"Broadcast records: {len(broadcasts)}\n"
        f"Migration: {migrated_note}"
    )


def analytics_text() -> str:
    lines = [
        "📊 Analytics\n",
        f"👥 Users: {len(registered_users)}",
        f"📁 Files: {len(file_store)}",
        f"🔗 Accesses: {total_accesses()}",
        f"📢 Broadcasts: {len(broadcasts)}",
        f"📺 Channels: {len(channels)}",
        "",
        "Most accessed files",
    ]
    ranked = sorted(file_store.items(), key=lambda pair: int(pair[1].get("access_count", 0) or 0), reverse=True)
    ranked = [pair for pair in ranked if int(pair[1].get("access_count", 0) or 0) > 0][:5]
    if not ranked:
        lines.append("No files found in database.")
    else:
        for fid, item in ranked:
            lines.append(f"• {fid} · {item.get('access_count', 0)} · {item.get('type')}")
    lines.append("\nRecent activity")
    if not activity:
        lines.append("No recent activity recorded in database.")
    else:
        for item in reversed(activity[-5:]):
            lines.append(f"• {fmt_dt(item.get('ts'))} · {item.get('kind')} · {item.get('detail')}")
    lines.append("\nBroadcast statistics")
    if not broadcasts:
        lines.append("No broadcasts recorded in database.")
    else:
        last = broadcasts[-1]
        sent_all = sum(int(x.get("sent", 0) or 0) for x in broadcasts)
        lines.append(f"• Last: {fmt_dt(last.get('timestamp'))} · {last.get('sent', 0)}/{last.get('total', 0)}")
        lines.append(f"• All-time sent: {sent_all}")
    lines.append("\nChannel statistics")
    if not channels:
        lines.append("No channels found in database.")
    else:
        lines.append(f"• Force Join: {len(forcejoin_list())}")
        lines.append(f"• Publishing: {len(publishing_list())}")
    return "\n".join(lines)


async def show_by_key(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, as_reply: bool = False) -> None:
    if key.startswith("file:d:"):
        await show_file_detail(update, context, key.split(":", 2)[2])
        return
    if key.startswith("ch:d:"):
        await show_channel_detail(update, key.split(":", 2)[2])
        return
    mapping = {
        "nav:main": (dashboard_text(), dashboard_markup()),
        "nav:files": (files_home_text(), files_home_markup()),
        "nav:settings": (settings_home_text(), settings_home_markup()),
        "set:general": (settings_general_text(), settings_general_markup()),
        "set:messages": (settings_messages_text(), settings_messages_markup()),
        "set:files": (settings_files_text(), settings_files_markup()),
        "set:channels": (settings_channels_text(), settings_channels_markup()),
        "set:admins": (settings_admins_text(), settings_admins_markup(update.effective_user.id if update.effective_user else 0)),
        "set:advanced": (settings_advanced_text(), kb([[b("⬅️ Back", "nav:settings")]])),
        "nav:broadcast": (
            "📢 Broadcast\n\nSend copyable Telegram content to every registered user.",
            broadcast_home_markup(),
        ),
        "nav:posts": (
            "📝 Posts\n\nCompose and publish content to selected channels.",
            kb([[b("📝 New Post", "post:new")], [b("⬅️ Back", "nav:main")]]),
        ),
        "nav:channels": (
            "📺 Channels\n\nAdd channels, manage Force Join, and control publishing targets.",
            channels_home_markup(),
        ),
        "nav:analytics": (analytics_text(), kb([[b("⬅️ Back", "nav:main")]])),
    }
    if key in mapping:
        text, markup = mapping[key]
        if as_reply and update.effective_message and not update.callback_query:
            await update.effective_message.reply_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        await present(update, text, markup)
        return
    await present(update, dashboard_text(), dashboard_markup())


async def route_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Optional[str] = None) -> None:
    query = update.callback_query
    if query and not is_admin(query.from_user.id):
        await deny(update)
        return
    data = data or (query.data if query else "")
    if data in (
        "nav:main",
        "nav:files",
        "nav:settings",
        "nav:broadcast",
        "nav:posts",
        "nav:channels",
        "nav:analytics",
        "set:general",
        "set:messages",
        "set:files",
        "set:channels",
        "set:admins",
        "set:advanced",
    ):
        if data == "nav:files":
            context.user_data.pop("file_search", None)
        await show_by_key(update, context, data)
        return
    if data.startswith("file:list:"):
        page = int(data.split(":")[2])
        await show_file_list(update, context, page)
        return
    if data.startswith("file:d:"):
        await show_file_detail(update, context, data.split(":", 2)[2])
        return
    if data.startswith("file:rmok:"):
        fid = data.split(":", 2)[2]
        if fid in file_store:
            del file_store[fid]
            file_access_count.pop(fid, None)
            save_data()
            log_activity("file_delete", fid, query.from_user.id if query else None)
            logger.info("Admin %s deleted file %s", query.from_user.id if query else "?", fid)
            await present(update, "File deleted.", kb([[b("⬅️ Files", "file:list:0")]]))
        else:
            await present(update, "No files found in database.", kb([[b("⬅️ Files", "file:list:0")]]))
        return
    if data.startswith("file:rm:"):
        fid = data.split(":", 2)[2]
        await present(
            update,
            "Delete this file permanently?",
            kb([
                [b("✅ Confirm", f"file:rmok:{fid}")],
                [b("❌ Cancel", f"file:d:{fid}")],
            ]),
        )
        return
    if data.startswith("ch:list:"):
        await show_channel_list(update, context, int(data.split(":")[2]), "all")
        return
    if data.startswith("ch:fjpage:"):
        await show_channel_list(update, context, int(data.split(":")[2]), "fj")
        return
    if data.startswith("ch:pubpage:"):
        await show_channel_list(update, context, int(data.split(":")[2]), "pub")
        return
    if data == "ch:fjlist":
        await show_channel_list(update, context, 0, "fj")
        return
    if data == "ch:publist":
        await show_channel_list(update, context, 0, "pub")
        return
    if data.startswith("ch:d:"):
        await show_channel_detail(update, data.split(":", 2)[2])
        return
    if data.startswith("ch:fj:"):
        ch = find_channel(data.split(":", 2)[2])
        if not ch:
            await present(update, "No channels found in database.", kb([[b("⬅️ Back", "ch:list:0")]]))
            return
        ch["force_join"] = not bool(ch.get("force_join"))
        save_data()
        await show_channel_detail(update, channel_key(ch))
        return
    if data.startswith("ch:pub:"):
        ch = find_channel(data.split(":", 2)[2])
        if not ch:
            await present(update, "No channels found in database.", kb([[b("⬅️ Back", "ch:list:0")]]))
            return
        ch["publishing"] = not bool(ch.get("publishing"))
        save_data()
        await show_channel_detail(update, channel_key(ch))
        return
    if data.startswith("ch:rmok:"):
        cid = data.split(":", 2)[2]
        ch = find_channel(cid)
        if not ch:
            await present(update, "No channels found in database.", kb([[b("⬅️ Back", "ch:list:0")]]))
            return
        channels.remove(ch)
        save_data()
        log_activity("channel_remove", cid, query.from_user.id if query else None)
        await present(update, "Channel removed.", kb([[b("⬅️ Channels", "nav:channels")]]))
        return
    if data.startswith("ch:rm:"):
        cid = data.split(":", 2)[2]
        await present(
            update,
            "Remove this channel from the database?",
            kb([
                [b("✅ Confirm", f"ch:rmok:{cid}")],
                [b("❌ Cancel", f"ch:d:{cid}")],
            ]),
        )
        return
    if data.startswith("bc:hist:"):
        await show_broadcast_history(update, int(data.split(":")[2]))
        return
    if data == "set:tog:register":
        settings["register_on_start"] = not bool(settings.get("register_on_start", True))
        save_data()
        await show_by_key(update, context, "set:general")
        return
    if data == "set:tog:fj":
        settings["force_join_enabled"] = not bool(settings.get("force_join_enabled", True))
        save_data()
        parent = "set:channels" if query and query.message and "Channels" in (query.message.text or "") else "set:general"
        if query and query.message and "General" in (query.message.text or ""):
            parent = "set:general"
        await show_by_key(update, context, parent)
        return
    if data == "set:tog:autodel":
        settings["auto_delete"] = not bool(settings.get("auto_delete", True))
        save_data()
        await show_by_key(update, context, "set:files")
        return
    if data == "set:tog:again":
        settings["access_again"] = not bool(settings.get("access_again", True))
        save_data()
        await show_by_key(update, context, "set:files")
        return
    if data == "set:rmlist":
        rows = []
        uid = query.from_user.id if query else 0
        for admin in admin_ids():
            if admin == owner_id() or admin == uid:
                continue
            rows.append([b(str(admin), f"set:rmask:{admin}")])
        rows.append([b("⬅️ Back", "set:admins")])
        await present(update, "Select an administrator to remove. The owner cannot be removed.", kb(rows))
        return
    if data.startswith("set:rmask:"):
        uid = data.split(":")[2]
        await present(
            update,
            f"Remove administrator {uid}?",
            kb([
                [b("✅ Confirm", f"set:rmok:{uid}")],
                [b("❌ Cancel", "set:admins")],
            ]),
        )
        return
    if data.startswith("set:rmok:"):
        try:
            uid = int(data.split(":")[2])
        except ValueError:
            await present(update, "Invalid administrator ID.", kb([[b("⬅️ Back", "set:admins")]]))
            return
        actor = query.from_user.id if query else 0
        if uid == owner_id():
            await present(update, "The owner cannot be removed.", settings_admins_markup(actor))
            return
        if uid == actor:
            await present(update, "You cannot remove yourself.", settings_admins_markup(actor))
            return
        admins = admin_ids()
        if uid in admins:
            if len(admins) <= 1:
                await present(update, "The last administrator cannot be removed.", settings_admins_markup(actor))
                return
            admins.remove(uid)
            settings["admins"] = admins
            save_data()
            logger.info("Admin %s removed administrator %s", actor, uid)
        await show_by_key(update, context, "set:admins")
        return
    if data == "noop":
        await answer(query)
        return
    await answer(query)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await route_admin(update, context)


def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    conv_fallbacks = [
        CommandHandler("cancel", global_cancel),
        CommandHandler("start", start),
        CallbackQueryHandler(cancel_callback, pattern=r"^(nav:|set:general|set:messages|set:files|set:channels|set:admins|set:advanced)"),
    ]
    wait_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(wait_start, pattern=r"^in:")],
        states={
            WAIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wait_receive),
                CommandHandler("skip", wait_skip),
            ],
        },
        fallbacks=conv_fallbacks,
        allow_reentry=True,
        name="wait_text",
    )
    gen_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(gen_start, pattern=r"^file:gen$")],
        states={
            GEN_CONTENT: [MessageHandler(~filters.COMMAND, gen_receive)],
        },
        fallbacks=conv_fallbacks,
        allow_reentry=True,
        name="gen_link",
    )
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ch_add_start, pattern=r"^ch:add$")],
        states={
            CH_REF: [MessageHandler(filters.TEXT & ~filters.COMMAND, ch_add_ref)],
            CH_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ch_add_url),
                CommandHandler("skip", ch_add_url_skip),
            ],
            CH_BTN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ch_add_btn)],
            CH_FJ: [CallbackQueryHandler(ch_add_fj, pattern=r"^ch:newfj:[01]$")],
            CH_PUB: [CallbackQueryHandler(ch_add_pub, pattern=r"^ch:newpub:[01]$")],
        },
        fallbacks=conv_fallbacks,
        allow_reentry=True,
        name="add_channel",
    )
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(bc_start, pattern=r"^bc:new$")],
        states={
            BC_CONTENT: [MessageHandler(~filters.COMMAND, bc_content)],
            BC_BTN_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bc_btn_text)],
            BC_BTN_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, bc_btn_url)],
            BC_PREVIEW: [CallbackQueryHandler(bc_preview_cb, pattern=r"^bc:(addbtn|preview|go|no)$")],
        },
        fallbacks=conv_fallbacks,
        allow_reentry=True,
        name="broadcast",
    )
    post_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(post_start, pattern=r"^post:new$")],
        states={
            POST_CHANNELS: [CallbackQueryHandler(post_channels_cb, pattern=r"^(post:ct:|post:addch|post:content|nav:posts)")],
            POST_CH_REF: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_add_channel)],
            POST_CONTENT: [MessageHandler(~filters.COMMAND, post_content)],
            POST_BTN_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_btn_text)],
            POST_BTN_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_btn_url)],
            POST_BTN_PLACE: [CallbackQueryHandler(post_btn_place_cb, pattern=r"^post:(addbtn|row:|brm:|preview|go|edit|edch|no)")],
            POST_PREVIEW: [CallbackQueryHandler(post_btn_place_cb, pattern=r"^post:(go|edit|no)")],
        },
        fallbacks=conv_fallbacks,
        allow_reentry=True,
        name="posts",
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(wait_conv)
    app.add_handler(gen_conv)
    app.add_handler(channel_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(post_conv)
    app.add_handler(CallbackQueryHandler(joined_callback, pattern=r"^join:ok:"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^(nav:|set:|file:|ch:|bc:hist:)"))
    app.add_handler(CommandHandler("cancel", global_cancel))
    return app


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_NEW_BOTFATHER_TOKEN_HERE":
        raise SystemExit("Set BOT_TOKEN before starting the bot.")
    app = build_application()
    logger.info("WLZBI Manager Bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
