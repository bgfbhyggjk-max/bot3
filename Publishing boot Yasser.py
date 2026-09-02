# -*- coding: utf-8 -*-
"""
Publishing boot Yasser
مصنع بوتات نشر - نسخة أولية قابلة للتشغيل

المتطلبات:
    pip install aiogram aiosqlite

التشغيل:
    1) ضع توكن البوت الصانع في FACTORY_TOKEN.
    2) ضع Telegram user ID الخاص بك في OWNER_ID.
    3) شغّل:
       python "Publishing boot Yasser.py"

ملاحظات:
- البوتات المصنوعة تستخدم Bot API وتعمل فقط في المحادثات التي يستطيع البوت الوصول إليها.
- لا يستخدم هذا المشروع تسجيل دخول حساب Telegram الشخصي.
- النشر التلقائي مصمم للمجموعات/القنوات التي أضيف إليها البوت ولديه صلاحية مناسبة.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# =========================
# الإعدادات
# =========================
FACTORY_TOKEN = "8324058809:AAF12vpKz2XRrIRUJ3vBCYZSuJGALv8Jm3g"
OWNER_ID = 342845021  # ضع هنا ID حسابك في Telegram

DB_PATH = Path(__file__).with_name("publishing_factory.db")
MAX_BROADCAST_DELAY = 86400
MIN_BROADCAST_DELAY = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("Publishing boot Yasser")

# =========================
# قاعدة البيانات
# =========================
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    bot_id INTEGER,
    username TEXT,
    owner_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_type TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    updated_at TEXT NOT NULL,
    UNIQUE(bot_id, chat_id),
    FOREIGN KEY(bot_id) REFERENCES bots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    delay_seconds INTEGER NOT NULL DEFAULT 60,
    created_at TEXT NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    bot_id INTEGER PRIMARY KEY,
    welcome_text TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(bot_id) REFERENCES bots(id) ON DELETE CASCADE
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()

async def db_execute(sql, params=(), fetch=False, fetchone=False):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        if fetchone:
            row = await cur.fetchone()
            await db.commit()
            return row
        if fetch:
            rows = await cur.fetchall()
            await db.commit()
            return rows
        await db.commit()
        return cur.lastrowid

async def now():
    return datetime.now(timezone.utc).isoformat()

# =========================
# الحالات
# =========================
class AddAdmin(StatesGroup):
    user_id = State()

class AddBot(StatesGroup):
    token = State()
    owner_id = State()

class WelcomeState(StatesGroup):
    bot_id = State()
    text = State()

class AddMessageState(StatesGroup):
    bot_id = State()
    text = State()
    delay = State()

class DeleteMessageState(StatesGroup):
    message_id = State()

# =========================
# صلاحيات
# =========================
async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    row = await db_execute(
        "SELECT 1 FROM admins WHERE user_id=?",
        (user_id,),
        fetchone=True,
    )
    return row is not None

async def is_bot_owner(user_id: int, bot_db_id: int) -> bool:
    row = await db_execute(
        "SELECT 1 FROM bots WHERE id=? AND owner_id=? AND status='active'",
        (bot_db_id, user_id),
        fetchone=True,
    )
    return row is not None

# =========================
# لوحة التحكم
# =========================
def admin_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="👤 إضافة أدمن", callback_data="add_admin")
    b.button(text="🤖 إضافة بوت", callback_data="add_bot")
    b.button(text="🤖 البوتات المصنوعة", callback_data="list_bots")
    b.button(text="💬 رسالة الترحيب", callback_data="welcome_menu")
    b.button(text="📤 الرسائل", callback_data="messages_menu")
    b.button(text="📊 الإحصائيات", callback_data="stats")
    b.adjust(2, 1, 2, 1)
    return b.as_markup()

def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")]
    ])

def bots_keyboard(rows):
    b = InlineKeyboardBuilder()
    for bot_id, username, owner_id, status in rows:
        state = "🟢" if status == "active" else "🔴"
        name = f"{state} @{username or 'بدون_معرف'}"
        b.button(text=name, callback_data=f"botinfo:{bot_id}")
    b.adjust(1)
    b.button(text="⬅️ رجوع", callback_data="home")
    return b.as_markup()

# =========================
# مصنع البوتات
# =========================
factory_bot: Bot | None = None
dp = Dispatcher()
router = Router()
dp.include_router(router)

running_tasks: dict[int, asyncio.Task] = {}
bot_instances: dict[int, Bot] = {}

async def validate_token(token: str):
    try:
        test_bot = Bot(token)
        me = await test_bot.get_me()
        await test_bot.session.close()
        return me
    except Exception:
        return None

async def register_chat(bot_db_id: int, chat: Message):
    title = chat.chat.title or chat.chat.full_name or ""
    await db_execute(
        """
        INSERT INTO chats(bot_id, chat_id, chat_type, title, status, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(bot_id, chat_id) DO UPDATE SET
          chat_type=excluded.chat_type,
          title=excluded.title,
          status='active',
          updated_at=excluded.updated_at
        """,
        (bot_db_id, chat.chat.id, chat.chat.type, title, "active", await now()),
    )

async def mark_chat_inactive(bot_db_id: int, chat_id: int):
    await db_execute(
        "UPDATE chats SET status='inactive', updated_at=? WHERE bot_id=? AND chat_id=?",
        (await now(), bot_db_id, chat_id),
    )

async def send_saved_messages(bot_db_id: int):
    """إرسال الرسائل المحفوظة للمحادثات النشطة.
    يعمل فقط على المحادثات التي سجّلها البوت وأصبح لديه صلاحية الإرسال فيها.
    """
    while True:
        bot_row = await db_execute(
            "SELECT token, status FROM bots WHERE id=?",
            (bot_db_id,),
            fetchone=True,
        )
        if not bot_row or bot_row[1] != "active":
            return

        msgs = await db_execute(
            "SELECT id, text, delay_seconds FROM messages WHERE bot_id=? ORDER BY id",
            (bot_db_id,),
            fetch=True,
        )
        if not msgs:
            await asyncio.sleep(10)
            continue

        chats = await db_execute(
            "SELECT chat_id FROM chats WHERE bot_id=? AND status='active'",
            (bot_db_id,),
            fetch=True,
        )
        if not chats:
            await asyncio.sleep(10)
            continue

        bot = bot_instances.get(bot_db_id)
        if bot is None:
            bot = Bot(bot_row[0])
            bot_instances[bot_db_id] = bot

        for _, text, delay in msgs:
            for (chat_id,) in chats:
                try:
                    await bot.send_message(chat_id, text)
                except Exception as exc:
                    # إذا لم يعد البوت قادرًا على الوصول للمحادثة، نوقفها محليًا.
                    msg = str(exc).lower()
                    if any(x in msg for x in (
                        "chat not found",
                        "bot was kicked",
                        "forbidden",
                        "not enough rights",
                        "user is deactivated",
                    )):
                        await mark_chat_inactive(bot_db_id, chat_id)
                    log.warning("Send failed bot=%s chat=%s: %s", bot_db_id, chat_id, exc)
            delay = max(MIN_BROADCAST_DELAY, min(int(delay), MAX_BROADCAST_DELAY))
            await asyncio.sleep(delay)

async def start_broadcast_task(bot_db_id: int):
    old = running_tasks.get(bot_db_id)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(send_saved_messages(bot_db_id))
    running_tasks[bot_db_id] = task

# =========================
# البوتات المصنوعة
# =========================
async def bot_startup(bot_db_id: int, token: str):
    if bot_db_id in bot_instances:
        return
    bot = Bot(token)
    bot_instances[bot_db_id] = bot

    local_dp = Dispatcher()
    local_router = Router()
    local_dp.include_router(local_router)

    @local_router.message(CommandStart())
    async def made_start(message: Message):
        row = await db_execute(
            "SELECT welcome_text FROM settings WHERE bot_id=?",
            (bot_db_id,),
            fetchone=True,
        )
        welcome = row[0] if row else ""
        if welcome:
            await message.answer(welcome)
        else:
            await message.answer("أهلًا بك 👋")

        # تسجيل الخاص/المجموعة التي استقبلت الرسالة
        try:
            await register_chat(bot_db_id, message)
        except Exception as exc:
            log.warning("chat registration failed: %s", exc)

    @local_router.message()
    async def made_message(message: Message):
        # إذا كانت الرسالة من مجموعة/قناة أو خاص، نحاول تسجيلها.
        try:
            await register_chat(bot_db_id, message)
        except Exception as exc:
            log.warning("chat registration failed: %s", exc)

    async def runner():
        try:
            await start_broadcast_task(bot_db_id)
            await local_dp.start_polling(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Made bot stopped: %s", bot_db_id)

    asyncio.create_task(runner())

async def load_all_bots():
    rows = await db_execute(
        "SELECT id, token FROM bots WHERE status='active'",
        fetch=True,
    )
    for bot_db_id, token in rows:
        asyncio.create_task(bot_startup(bot_db_id, token))

# =========================
# أوامر المصنع
# =========================
@router.message(CommandStart())
async def start(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("❌ ليس لديك صلاحية الدخول إلى لوحة التحكم.")
        return
    await message.answer(
        "🏭 <b>مصنع البوتات</b>\n\n"
        "اختر العملية المطلوبة من لوحة التحكم:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )

@router.callback_query(F.data == "home")
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await is_admin(call.from_user.id):
        await call.answer("غير مصرح", show_alert=True)
        return
    await call.message.edit_text("🏭 <b>لوحة تحكم المصنع</b>", reply_markup=admin_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("تم الإلغاء.", reply_markup=admin_keyboard())
    await call.answer()

# إضافة أدمن
@router.callback_query(F.data == "add_admin")
async def add_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer("فقط المالك يستطيع إضافة أدمن.", show_alert=True)
        return
    await state.set_state(AddAdmin.user_id)
    await call.message.edit_text("👤 أرسل ID الأدمن:", reply_markup=cancel_keyboard())
    await call.answer()

@router.message(AddAdmin.user_id)
async def add_admin_id(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل رقم ID صحيح.")
        return
    await db_execute(
        "INSERT OR REPLACE INTO admins(user_id, added_at) VALUES(?,?)",
        (uid, await now()),
    )
    await state.clear()
    await message.answer("✅ تمت إضافة الأدمن.", reply_markup=admin_keyboard())

# إضافة بوت
@router.callback_query(F.data == "add_bot")
async def add_bot(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return
    await state.set_state(AddBot.token)
    await call.message.edit_text(
        "🤖 أرسل Token البوت المصنوع.\n\n"
        "سيتم التحقق منه قبل حفظه.",
        reply_markup=cancel_keyboard(),
    )
    await call.answer()

@router.message(AddBot.token)
async def add_bot_token(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    token = message.text.strip()
    me = await validate_token(token)
    if not me:
        await message.answer("❌ التوكن غير صالح أو لا يمكن الاتصال بالبوت.")
        return
    await state.update_data(token=token, username=me.username, bot_id=me.id)
    await state.set_state(AddBot.owner_id)
    await message.answer(
        f"✅ تم التحقق من البوت: @{me.username or 'بدون معرف'}\n\n"
        "الآن أرسل ID مالك البوت الذي سيتحكم به."
    )

@router.message(AddBot.owner_id)
async def add_bot_owner(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    try:
        owner_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل ID رقمي صحيح.")
        return

    data = await state.get_data()
    token = data["token"]
    username = data["username"]
    bot_id = data["bot_id"]

    try:
        db_bot_id = await db_execute(
            """
            INSERT INTO bots(token, bot_id, username, owner_id, status, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (token, bot_id, username, owner_id, "active", await now()),
        )
        await db_execute(
            "INSERT OR REPLACE INTO settings(bot_id, welcome_text) VALUES(?,?)",
            (db_bot_id, "أهلًا بك 👋"),
        )
    except Exception:
        await message.answer("❌ هذا البوت مضاف مسبقًا.")
        await state.clear()
        return

    await state.clear()
    asyncio.create_task(bot_startup(db_bot_id, token))
    await message.answer(
        f"✅ تم إنشاء إعدادات البوت @{username or 'بدون معرف'}\n"
        f"👤 المالك: <code>{owner_id}</code>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )

# قائمة البوتات
@router.callback_query(F.data == "list_bots")
async def list_bots(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return
    rows = await db_execute(
        "SELECT id, username, owner_id, status FROM bots ORDER BY id DESC",
        fetch=True,
    )
    if not rows:
        await call.message.edit_text("لا توجد بوتات مضافة.", reply_markup=admin_keyboard())
    else:
        await call.message.edit_text("🤖 <b>البوتات المصنوعة:</b>", reply_markup=bots_keyboard(rows), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("botinfo:"))
async def bot_info(call: CallbackQuery):
    bot_db_id = int(call.data.split(":")[1])
    if not await is_admin(call.from_user.id) and not await is_bot_owner(call.from_user.id, bot_db_id):
        await call.answer("غير مصرح", show_alert=True)
        return

    row = await db_execute(
        "SELECT username, owner_id, status, created_at FROM bots WHERE id=?",
        (bot_db_id,),
        fetchone=True,
    )
    if not row:
        await call.answer("البوت غير موجود.", show_alert=True)
        return

    username, owner_id, status, created_at = row
    chats = await db_execute(
        "SELECT COUNT(*) FROM chats WHERE bot_id=? AND status='active'",
        (bot_db_id,),
        fetchone=True,
    )
    msgs = await db_execute(
        "SELECT COUNT(*) FROM messages WHERE bot_id=?",
        (bot_db_id,),
        fetchone=True,
    )

    kb = InlineKeyboardBuilder()
    if status == "active":
        kb.button(text="⛔ تعطيل", callback_data=f"disable:{bot_db_id}")
    else:
        kb.button(text="▶️ تشغيل", callback_data=f"enable:{bot_db_id}")
    kb.button(text="🗑 حذف البوت", callback_data=f"deletebot:{bot_db_id}")
    kb.button(text="⬅️ رجوع", callback_data="list_bots")
    kb.adjust(1)

    text = (
        f"🤖 <b>@{username or 'بدون معرف'}</b>\n"
        f"👤 المالك: <code>{owner_id}</code>\n"
        f"📌 الحالة: {status}\n"
        f"👥 المجموعات/المحادثات النشطة: {chats[0]}\n"
        f"💬 الرسائل المحفوظة: {msgs[0]}\n"
        f"🕒 الإنشاء: {created_at}"
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("disable:"))
async def disable_bot(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("المالك الرئيسي فقط.", show_alert=True)
        return
    bot_db_id = int(call.data.split(":")[1])
    await db_execute("UPDATE bots SET status='disabled' WHERE id=?", (bot_db_id,))
    task = running_tasks.get(bot_db_id)
    if task and not task.done():
        task.cancel()
    await call.answer("تم التعطيل.")
    await list_bots(call)

@router.callback_query(F.data.startswith("enable:"))
async def enable_bot(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("المالك الرئيسي فقط.", show_alert=True)
        return
    bot_db_id = int(call.data.split(":")[1])
    row = await db_execute("SELECT token FROM bots WHERE id=?", (bot_db_id,), fetchone=True)
    if not row:
        await call.answer("غير موجود.", show_alert=True)
        return
    await db_execute("UPDATE bots SET status='active' WHERE id=?", (bot_db_id,))
    asyncio.create_task(bot_startup(bot_db_id, row[0]))
    await call.answer("تم التشغيل.")
    await list_bots(call)

@router.callback_query(F.data.startswith("deletebot:"))
async def delete_bot(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("المالك الرئيسي فقط.", show_alert=True)
        return
    bot_db_id = int(call.data.split(":")[1])
    task = running_tasks.get(bot_db_id)
    if task and not task.done():
        task.cancel()
    bot = bot_instances.pop(bot_db_id, None)
    if bot:
        try:
            await bot.session.close()
        except Exception:
            pass
    await db_execute("DELETE FROM bots WHERE id=?", (bot_db_id,))
    await call.answer("تم حذف البوت.")
    await list_bots(call)

# الترحيب
@router.callback_query(F.data == "welcome_menu")
async def welcome_menu(call: CallbackQuery):
    rows = await db_execute(
        "SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC",
        fetch=True,
    )
    b = InlineKeyboardBuilder()
    for bot_id, username in rows:
        b.button(text=f"🤖 @{username or 'بدون معرف'}", callback_data=f"welcome:{bot_id}")
    b.button(text="⬅️ رجوع", callback_data="home")
    b.adjust(1)
    await call.message.edit_text("💬 اختر البوت لتعديل رسالة الترحيب:", reply_markup=b.as_markup())
    await call.answer()

@router.callback_query(F.data.startswith("welcome:"))
async def welcome_choose(call: CallbackQuery, state: FSMContext):
    bot_db_id = int(call.data.split(":")[1])
    if not await is_admin(call.from_user.id) and not await is_bot_owner(call.from_user.id, bot_db_id):
        await call.answer("غير مصرح", show_alert=True)
        return
    await state.set_state(WelcomeState.text)
    await state.update_data(bot_id=bot_db_id)
    await call.message.edit_text("✏️ أرسل رسالة الترحيب الجديدة:", reply_markup=cancel_keyboard())
    await call.answer()

@router.message(WelcomeState.text)
async def welcome_save(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_db_id = data["bot_id"]
    if not await is_admin(message.from_user.id) and not await is_bot_owner(message.from_user.id, bot_db_id):
        return
    await db_execute(
        "INSERT OR REPLACE INTO settings(bot_id, welcome_text) VALUES(?,?)",
        (bot_db_id, message.text or ""),
    )
    await state.clear()
    await message.answer("✅ تم تحديث رسالة الترحيب.", reply_markup=admin_keyboard())

# الرسائل
@router.callback_query(F.data == "messages_menu")
async def messages_menu(call: CallbackQuery):
    rows = await db_execute(
        "SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC",
        fetch=True,
    )
    b = InlineKeyboardBuilder()
    b.button(text="➕ إضافة رسالة", callback_data="add_message_select")
    b.button(text="📋 الرسائل المضافة", callback_data="saved_messages_select")
    b.button(text="⬅️ رجوع", callback_data="home")
    b.adjust(1)
    await call.message.edit_text("📤 <b>إدارة الرسائل</b>", reply_markup=b.as_markup(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "add_message_select")
async def add_message_select(call: CallbackQuery):
    rows = await db_execute(
        "SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC",
        fetch=True,
    )
    b = InlineKeyboardBuilder()
    for bot_id, username in rows:
        b.button(text=f"🤖 @{username or 'بدون معرف'}", callback_data=f"addmsg:{bot_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text("اختر البوت الذي ستُضاف له الرسالة:", reply_markup=b.as_markup())
    await call.answer()

@router.callback_query(F.data.startswith("addmsg:"))
async def addmsg_start(call: CallbackQuery, state: FSMContext):
    bot_db_id = int(call.data.split(":")[1])
    if not await is_admin(call.from_user.id) and not await is_bot_owner(call.from_user.id, bot_db_id):
        await call.answer("غير مصرح", show_alert=True)
        return
    await state.set_state(AddMessageState.text)
    await state.update_data(bot_id=bot_db_id)
    await call.message.edit_text("📝 أرسل نص الرسالة:", reply_markup=cancel_keyboard())
    await call.answer()

@router.message(AddMessageState.text)
async def addmsg_text(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_db_id = data["bot_id"]
    if not await is_admin(message.from_user.id) and not await is_bot_owner(message.from_user.id, bot_db_id):
        return
    await state.update_data(text=message.text or "")
    await state.set_state(AddMessageState.delay)
    await message.answer(
        f"⏱ أرسل الفاصل بالثواني بين الرسائل.\n"
        f"الحد الأدنى الآمن في هذا المشروع: {MIN_BROADCAST_DELAY} ثوانٍ."
    )

@router.message(AddMessageState.delay)
async def addmsg_delay(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_db_id = data["bot_id"]
    if not await is_admin(message.from_user.id) and not await is_bot_owner(message.from_user.id, bot_db_id):
        return
    try:
        delay = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل رقمًا صحيحًا.")
        return
    if delay < MIN_BROADCAST_DELAY or delay > MAX_BROADCAST_DELAY:
        await message.answer(
            f"❌ الفاصل يجب أن يكون بين {MIN_BROADCAST_DELAY} و {MAX_BROADCAST_DELAY} ثانية."
        )
        return
    await db_execute(
        "INSERT INTO messages(bot_id, text, delay_seconds, created_at) VALUES(?,?,?,?)",
        (bot_db_id, data["text"], delay, await now()),
    )
    await state.clear()
    await message.answer("✅ تمت إضافة الرسالة.", reply_markup=admin_keyboard())

@router.callback_query(F.data == "saved_messages_select")
async def saved_messages_select(call: CallbackQuery):
    rows = await db_execute(
        "SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC",
        fetch=True,
    )
    b = InlineKeyboardBuilder()
    for bot_id, username in rows:
        b.button(text=f"🤖 @{username or 'بدون معرف'}", callback_data=f"savedmsg:{bot_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text("اختر البوت:", reply_markup=b.as_markup())
    await call.answer()

@router.callback_query(F.data.startswith("savedmsg:"))
async def saved_messages(call: CallbackQuery):
    bot_db_id = int(call.data.split(":")[1])
    if not await is_admin(call.from_user.id) and not await is_bot_owner(call.from_user.id, bot_db_id):
        await call.answer("غير مصرح", show_alert=True)
        return
    rows = await db_execute(
        "SELECT id, text, delay_seconds FROM messages WHERE bot_id=? ORDER BY id",
        (bot_db_id,),
        fetch=True,
    )
    b = InlineKeyboardBuilder()
    text = "📋 <b>الرسائل المضافة</b>\n\n"
    if not rows:
        text += "لا توجد رسائل."
    else:
        for mid, msg_text, delay in rows:
            preview = (msg_text or "").replace("\n", " ")[:35]
            text += f"🆔 {mid} | ⏱ {delay}s | {preview}\n"
            b.button(text=f"🗑 حذف #{mid}", callback_data=f"delmsg:{mid}:{bot_db_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("delmsg:"))
async def delete_message(call: CallbackQuery):
    _, mid, bot_db_id = call.data.split(":")
    bot_db_id = int(bot_db_id)
    if not await is_admin(call.from_user.id) and not await is_bot_owner(call.from_user.id, bot_db_id):
        await call.answer("غير مصرح", show_alert=True)
        return
    await db_execute("DELETE FROM messages WHERE id=? AND bot_id=?", (int(mid), bot_db_id))
    await call.answer("تم حذف الرسالة.")
    await saved_messages(call)

# الإحصائيات
@router.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return
    bots = await db_execute("SELECT COUNT(*) FROM bots", fetchone=True)
    active_bots = await db_execute("SELECT COUNT(*) FROM bots WHERE status='active'", fetchone=True)
    active_chats = await db_execute("SELECT COUNT(*) FROM chats WHERE status='active'", fetchone=True)
    inactive_chats = await db_execute("SELECT COUNT(*) FROM chats WHERE status='inactive'", fetchone=True)
    total_msgs = await db_execute("SELECT COUNT(*) FROM messages", fetchone=True)

    text = (
        "📊 <b>إحصائيات المصنع</b>\n\n"
        f"🤖 إجمالي البوتات: {bots[0]}\n"
        f"🟢 البوتات النشطة: {active_bots[0]}\n"
        f"👥 المحادثات النشطة: {active_chats[0]}\n"
        f"🔴 المحادثات غير النشطة: {inactive_chats[0]}\n"
        f"💬 إجمالي الرسائل المحفوظة: {total_msgs[0]}\n\n"
        "🔄 يتم تحديث حالة المحادثة عند محاولة الإرسال إليها."
    )
    b = InlineKeyboardBuilder()
    b.button(text="🔄 تحديث", callback_data="stats")
    b.button(text="⬅️ رجوع", callback_data="home")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")
    await call.answer()

# =========================
# تشغيل
# =========================
async def main():
    if FACTORY_TOKEN == "PUT_FACTORY_BOT_TOKEN_HERE":
        raise RuntimeError("ضع توكن البوت الصانع داخل FACTORY_TOKEN قبل التشغيل.")
    if OWNER_ID == 0:
        raise RuntimeError("ضع ID حساب المالك داخل OWNER_ID قبل التشغيل.")

    await db_init()

    global factory_bot
    factory_bot = Bot(FACTORY_TOKEN)

    await load_all_bots()

    me = await factory_bot.get_me()
    log.info("Factory started: @%s", me.username)

    try:
        await dp.start_polling(factory_bot)
    finally:
        await factory_bot.session.close()
        for bot in list(bot_instances.values()):
            try:
                await bot.session.close()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
