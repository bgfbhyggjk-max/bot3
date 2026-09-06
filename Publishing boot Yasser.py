# -*- coding: utf-8 -*-
import asyncio
import logging
import os
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
FACTORY_TOKEN = "8276217141:AAHRxEK37ExpX2whGL6WIALNGU0kaVwiIFA"
OWNER_ID = 342845021 

DB_PATH = Path(__file__).with_name("publishing_factory.db")
MAX_BROADCAST_DELAY = 86400
MIN_BROADCAST_DELAY = 2

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

CREATE TABLE IF NOT EXISTS factory_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()
        await db.execute(
            "INSERT OR IGNORE INTO factory_settings(key, value) VALUES('global_welcome', ?)",
            ("أهلاً بك في البوت! 👋\nنتشرف بوجودك معنا.",)
        )
        await db.execute(
            "INSERT OR IGNORE INTO factory_settings(key, value) VALUES('force_channel', ?)",
            ("",)
        )
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

async def get_global_welcome():
    row = await db_execute("SELECT value FROM factory_settings WHERE key='global_welcome'", fetchone=True)
    return row[0] if row else "أهلاً بك 👋"

async def get_force_channel():
    row = await db_execute("SELECT value FROM factory_settings WHERE key='force_channel'", fetchone=True)
    return row[0] if row else ""

async def check_subscription(bot: Bot, user_id: int) -> bool:
    channel = await get_force_channel()
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            return True
        return False
    except Exception as e:
        log.warning(f"خطأ في فحص الاشتراك القناة {channel}: {e}")
        return True

# =========================
# الحالات FSM
# =========================
class ForceSubState(StatesGroup):
    channel = State()

class GlobalWelcomeState(StatesGroup):
    text = State()

class AddAdmin(StatesGroup):
    user_id = State()

class AddBot(StatesGroup):
    token = State()
    owner_id = State()

class SubAddPostState(StatesGroup):
    text = State()
    delay = State()

class AddMessageState(StatesGroup):
    bot_id = State()
    text = State()
    delay = State()

# =========================
# لوحات التحكم
# =========================
async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    row = await db_execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,), fetchone=True)
    return row is not None

def admin_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="👤 إضافة أدمن للمصنع", callback_data="add_admin")
    b.button(text="🤖 إضافة بوت جديد", callback_data="add_bot")
    b.button(text="🤖 البوتات المصنوعة", callback_data="list_bots")
    b.button(text="💬 الترحيب الموحد للجميع", callback_data="global_welcome_menu")
    b.button(text="📢 الاشتراك الإجباري الموحد", callback_data="global_force_sub_menu")
    b.button(text="📤 إدارة الرسائل والنشر", callback_data="messages_menu")
    b.button(text="📊 الإحصائيات", callback_data="stats")
    b.adjust(2, 1, 2, 1, 1)
    return b.as_markup()

def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")]])

def sub_bot_main_menu():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 نشر تلقائي", callback_data="sub_auto_publish")
    b.button(text="📢 إذاعة", callback_data="sub_broadcast")
    b.button(text="⚙️ الإعدادات", callback_data="sub_settings")
    b.button(text="📊 الإحصائيات", callback_data="sub_stats")
    b.adjust(2, 2)
    return b.as_markup()

def sub_bot_auto_publish_menu(msg_count: int):
    b = InlineKeyboardBuilder()
    b.button(text="➕ إنشاء منشور", callback_data="sub_create_post")
    b.button(text="📋 عرض المنشورات", callback_data="sub_list_posts")
    b.button(text="🔙 رجوع", callback_data="sub_main_menu")
    b.adjust(1)
    return b.as_markup()

def bots_keyboard(rows):
    b = InlineKeyboardBuilder()
    for bot_id, username, owner_id, status in rows:
        state = "🟢" if status == "active" else "🔴"
        name = f"{state} @{username or 'بدون_معرف'} (مالك: {owner_id})"
        b.button(text=name, callback_data=f"botinfo:{bot_id}")
    b.adjust(1)
    b.button(text="⬅️ رجوع", callback_data="home")
    return b.as_markup()

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

async def register_chat(bot_db_id: int, chat_id: int, chat_type: str, title: str):
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
        (bot_db_id, chat_id, chat_type, title, "active", await now()),
    )

async def safe_send_message(bot: Bot, chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:
        log.warning("فشل الإرسال للمحادثة %s: %s", chat_id, exc)

async def send_saved_messages(bot_db_id: int):
    while True:
        await asyncio.sleep(1)
        bot_row = await db_execute("SELECT token, status FROM bots WHERE id=?", (bot_db_id,), fetchone=True)
        if not bot_row or bot_row[1] != "active":
            return

        msgs = await db_execute("SELECT id, text, delay_seconds FROM messages WHERE bot_id=? ORDER BY id", (bot_db_id,), fetch=True)
        if not msgs:
            await asyncio.sleep(5)
            continue

        chats = await db_execute("SELECT chat_id FROM chats WHERE bot_id=? AND status='active'", (bot_db_id,), fetch=True)
        if not chats:
            await asyncio.sleep(5)
            continue

        bot = bot_instances.get(bot_db_id)
        if bot is None:
            bot = Bot(bot_row[0])
            bot_instances[bot_db_id] = bot

        for _, text, delay in msgs:
            for (chat_id,) in chats:
                asyncio.create_task(safe_send_message(bot, chat_id, text))

            delay = max(MIN_BROADCAST_DELAY, min(int(delay), MAX_BROADCAST_DELAY))
            await asyncio.sleep(delay)

async def start_broadcast_task(bot_db_id: int):
    old = running_tasks.get(bot_db_id)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(send_saved_messages(bot_db_id))
    running_tasks[bot_db_id] = task

# =========================
# تشغيل البوتات الفرعية المصنوعة
# =========================
async def bot_startup(bot_db_id: int, token: str):
    if bot_db_id in bot_instances:
        return
    bot = Bot(token)
    bot_instances[bot_db_id] = bot

    local_dp = Dispatcher()
    local_router = Router()
    local_dp.include_router(local_router)

    async def is_bot_owner(user_id: int) -> bool:
        row = await db_execute("SELECT owner_id FROM bots WHERE id=?", (bot_db_id,), fetchone=True)
        return row and row[0] == user_id

    @local_router.message(CommandStart())
    async def made_start(message: Message):
        await register_chat(bot_db_id, message.chat.id, message.chat.type, message.chat.title or message.chat.full_name or "")
        
        if await is_bot_owner(message.from_user.id):
            text = "👋 <b>لوحة التحكم</b>\n\nاختر من القائمة:"
            await message.answer(text, reply_markup=sub_bot_main_menu(), parse_mode="HTML")
            return

        is_subbed = await check_subscription(bot, message.from_user.id)
        if not is_subbed:
            channel = await get_force_channel()
            ch_username = channel.replace("@", "")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 اشترك في القناة أولاً", url=f"https://t.me/{ch_username}")],
                [InlineKeyboardButton(text="✅ تحقق من الاشتراك", callback_data="check_sub")]
            ])
            await message.answer("⚠️ عذراً، يجب عليك الاشتراك في القناة التالية لاستخدام البوت:", reply_markup=kb)
            return

        welcome = await get_global_welcome()
        await message.answer(welcome)

    @local_router.callback_query(F.data == "check_sub")
    async def check_sub_callback(call: CallbackQuery):
        await call.answer()
        is_subbed = await check_subscription(bot, call.from_user.id)
        if is_subbed:
            await call.message.delete()
            welcome = await get_global_welcome()
            await call.message.answer(welcome)
        else:
            await call.answer("❌ لم تشترك في القناة بعد!", show_alert=True)

    @local_router.callback_query(F.data == "sub_main_menu")
    async def sub_main_menu(call: CallbackQuery):
        await call.answer()
        if not await is_bot_owner(call.from_user.id):
            return
        text = "👋 <b>لوحة التحكم</b>\n\nاختر من القائمة:"
        await call.message.edit_text(text, reply_markup=sub_bot_main_menu(), parse_mode="HTML")

    @local_router.callback_query(F.data == "sub_auto_publish")
    async def sub_auto_publish(call: CallbackQuery):
        await call.answer()
        if not await is_bot_owner(call.from_user.id):
            return
        msgs = await db_execute("SELECT COUNT(*) FROM messages WHERE bot_id=?", (bot_db_id,), fetchone=True)
        count = msgs[0] if msgs else 0
        text = f"🔄 <b>النشر التلقائي</b>\n\nعدد المنشورات: {count}"
        await call.message.edit_text(text, reply_markup=sub_bot_auto_publish_menu(count), parse_mode="HTML")

    @local_router.callback_query(F.data == "sub_create_post")
    async def sub_create_post(call: CallbackQuery, state: FSMContext):
        await call.answer()
        if not await is_bot_owner(call.from_user.id):
            return
        await state.set_state(SubAddPostState.text)
        await call.message.edit_text("📝 أرسل الآن نص المنشور الذي تريد نشره تلقائياً:")

    @local_router.message(SubAddPostState.text)
    async def sub_save_post_text(message: Message, state: FSMContext):
        if not await is_bot_owner(message.from_user.id):
            return
        await state.update_data(text=message.text)
        await state.set_state(SubAddPostState.delay)
        await message.answer("⏱ أرسل الآن الفاصل الزمني بالنشر بالثواني (مثال: 60):")

    @local_router.message(SubAddPostState.delay)
    async def sub_save_post_delay(message: Message, state: FSMContext):
        if not await is_bot_owner(message.from_user.id):
            return
        try:
            delay = int(message.text.strip())
        except ValueError:
            await message.answer("❌ يرجى إدخال رقم صحيح بالثواني.")
            return

        data = await state.get_data()
        await db_execute("INSERT INTO messages(bot_id, text, delay_seconds, created_at) VALUES(?,?,?,?)",
                         (bot_db_id, data["text"], delay, await now()))
        await state.clear()
        await message.answer("✅ تم إنشاء المنشور وجدولته بنجاح!", reply_markup=sub_bot_main_menu())

    @local_router.callback_query(F.data == "sub_list_posts")
    async def sub_list_posts(call: CallbackQuery):
        await call.answer()
        if not await is_bot_owner(call.from_user.id):
            return
        rows = await db_execute("SELECT id, text, delay_seconds FROM messages WHERE bot_id=? ORDER BY id", (bot_db_id,), fetch=True)
        if not rows:
            text = "📋 <b>المنشورات المضافة:</b>\n\nلا توجد منشورات حالياً."
        else:
            text = "📋 <b>قائمة المنشورات:</b>\n\n"
            for mid, mtext, mdelay in rows:
                prev = mtext.replace('\n', ' ')[:25]
                text += f"🆔 #{mid} | ⏱ كل {mdelay} ثانية | {prev}\n"
        
        b = InlineKeyboardBuilder()
        b.button(text="🔙 رجوع", callback_data="sub_auto_publish")
        await call.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")

    @local_router.message()
    async def made_message(message: Message):
        title = message.chat.title or message.chat.full_name or "محادثة"
        await register_chat(bot_db_id, message.chat.id, message.chat.type, title)

    async def runner():
        try:
            await start_broadcast_task(bot_db_id)
            await local_dp.start_polling(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("توقف البوت رقم: %s", bot_db_id)

    asyncio.create_task(runner())

async def load_all_bots():
    rows = await db_execute("SELECT id, token FROM bots WHERE status='active'", fetch=True)
    for bot_db_id, token in rows:
        asyncio.create_task(bot_startup(bot_db_id, token))

# =========================
# أوامر البوت الرئيسي (الصانع)
# =========================
@router.message(CommandStart())
async def start(message: Message):
    welcome = await get_global_welcome()
    if await is_admin(message.from_user.id):
        await message.answer(f"{welcome}\n\n🏭 <b>لوحة تحكم مصنع البوتات:</b>", reply_markup=admin_keyboard(), parse_mode="HTML")
    else:
        await message.answer(welcome)

@router.callback_query(F.data == "home")
async def home(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    if not await is_admin(call.from_user.id):
        return
    await call.message.edit_text("🏭 <b>لوحة تحكم مصنع البوتات</b>", reply_markup=admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.edit_text("تم الإلغاء.", reply_markup=admin_keyboard())

@router.callback_query(F.data == "global_force_sub_menu")
async def global_force_sub_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id):
        return
    curr_chan = await get_force_channel()
    await state.set_state(ForceSubState.channel)
    text = (
        "📢 <b>إدارة الاشتراك الإجباري الموحد لكل البوتات:</b>\n\n"
        f"<b>القناة الحالية:</b> {curr_chan if curr_chan else 'لا توجد'}\n\n"
        "✏️ أرسل معرف القناة الجديد شامل الـ @ (مثال: <code>@MyChannel</code>):\n"
        "أو أرسل <b>إلغاء</b> لحذف الاشتراك الإجباري."
    )
    await call.message.edit_text(text, reply_markup=cancel_keyboard(), parse_mode="HTML")

@router.message(ForceSubState.channel)
async def save_force_sub(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    text_input = message.text.strip()
    if text_input in ["إلغاء", "تعطيل", "حذف"]:
        await db_execute("INSERT OR REPLACE INTO factory_settings(key, value) VALUES('force_channel', '')")
        await state.clear()
        await message.answer("✅ تم تعطيل الاشتراك الإجباري بنجاح لكل البوتات!", reply_markup=admin_keyboard())
        return

    if not text_input.startswith("@"):
        await message.answer("❌ يجب أن يبدأ معرف القناة بـ @ (مثال: @MyChannel)")
        return

    await db_execute("INSERT OR REPLACE INTO factory_settings(key, value) VALUES('force_channel', ?)", (text_input,))
    await state.clear()
    await message.answer(f"✅ تم حفظ القناة {text_input} كاشتراك إجباري موحد لجميع البوتات!\n⚠️ تنبيه: تأكد من رفع البوتات مشرفين بالقناة.", reply_markup=admin_keyboard())

@router.callback_query(F.data == "global_welcome_menu")
async def global_welcome_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id):
        return
    current_welcome = await get_global_welcome()
    await state.set_state(GlobalWelcomeState.text)

    text = (
        "💬 <b>إدارة الترحيب الموحد (لكل البوتات + البوت الصانع):</b>\n\n"
        f"<b>الرسالة الحالية:</b>\n{current_welcome}\n\n"
        "✏️ أرسل نص الترحيب الجديد الذي تريده أن يظهر للجميع عند الضغط على /start:"
    )
    await call.message.edit_text(text, reply_markup=cancel_keyboard(), parse_mode="HTML")

@router.message(GlobalWelcomeState.text)
async def global_welcome_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    new_text = message.text or ""
    await db_execute(
        "INSERT OR REPLACE INTO factory_settings(key, value) VALUES('global_welcome', ?)",
        (new_text,)
    )
    await state.clear()
    await message.answer("✅ تم تحديث رسالة الترحيب الموحدة بنجاح لكل البوتات!", reply_markup=admin_keyboard())

@router.callback_query(F.data == "add_admin")
async def add_admin(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if call.from_user.id != OWNER_ID:
        await call.answer("المالك الرئيسي فقط يضيف أدمن.", show_alert=True)
        return
    await state.set_state(AddAdmin.user_id)
    await call.message.edit_text("👤 أرسل ID الأدمن الجديد:", reply_markup=cancel_keyboard())

@router.message(AddAdmin.user_id)
async def add_admin_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل ID رقمي صحيح.")
        return
    await db_execute("INSERT OR REPLACE INTO admins(user_id, added_at) VALUES(?,?)", (uid, await now()))
    await state.clear()
    await message.answer("✅ تمت إضافة الأدمن بنجاح.", reply_markup=admin_keyboard())

@router.callback_query(F.data == "add_bot")
async def add_bot(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id):
        return
    await state.set_state(AddBot.token)
    await call.message.edit_text("🤖 أرسل Token البوت الجديد:", reply_markup=cancel_keyboard())

@router.message(AddBot.token)
async def add_bot_token(message: Message, state: FSMContext):
    token = message.text.strip()
    me = await validate_token(token)
    if not me:
        await message.answer("❌ توكن غير صالح.")
        return
    await state.update_data(token=token, username=me.username, bot_id=me.id)
    await state.set_state(AddBot.owner_id)
    await message.answer(f"✅ تم التحقق من البوت: @{me.username}\n\nأرسل الآن Telegram ID الخاص بمالك هذا البوت لترقيته كـ Admin تلقائياً:")

@router.message(AddBot.owner_id)
async def add_bot_owner(message: Message, state: FSMContext):
    try:
        owner_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل ID صحيح.")
        return

    data = await state.get_data()
    token, username, bot_id = data["token"], data["username"], data["bot_id"]

    try:
        db_bot_id = await db_execute(
            "INSERT INTO bots(token, bot_id, username, owner_id, status, created_at) VALUES(?,?,?,?,?,?)",
            (token, bot_id, username, owner_id, "active", await now()),
        )
        await db_execute("INSERT OR REPLACE INTO admins(user_id, added_at) VALUES(?,?)", (owner_id, await now()))
    except Exception:
        await message.answer("❌ هذا البوت مضاف مسبقاً.")
        await state.clear()
        return

    await state.clear()
    asyncio.create_task(bot_startup(db_bot_id, token))
    await message.answer(f"✅ تم إنشاء البوت @{username}\n👤 وتم رفع المالك (ID: <code>{owner_id}</code>) كـ Admin تلقائياً!", reply_markup=admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "list_bots")
async def list_bots(call: CallbackQuery):
    await call.answer()
    rows = await db_execute("SELECT id, username, owner_id, status FROM bots ORDER BY id DESC", fetch=True)
    if not rows:
        await call.message.edit_text("لا توجد بوتات مضافة.", reply_markup=admin_keyboard())
    else:
        await call.message.edit_text("🤖 <b>قائمة البوتات المصنوعة:</b>", reply_markup=bots_keyboard(rows), parse_mode="HTML")

@router.callback_query(F.data.startswith("botinfo:"))
async def bot_info(call: CallbackQuery):
    await call.answer()
    bot_db_id = int(call.data.split(":")[1])
    row = await db_execute("SELECT username, owner_id, status, created_at FROM bots WHERE id=?", (bot_db_id,), fetchone=True)
    if not row:
        return

    username, owner_id, status, created_at = row
    chats = await db_execute("SELECT COUNT(*) FROM chats WHERE bot_id=? AND status='active'", (bot_db_id,), fetchone=True)
    msgs = await db_execute("SELECT COUNT(*) FROM messages WHERE bot_id=?", (bot_db_id,), fetchone=True)

    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 حذف البوت", callback_data=f"deletebot:{bot_db_id}")
    kb.button(text="⬅️ رجوع", callback_data="list_bots")
    kb.adjust(1)

    text = (
        f"🤖 <b>البوت: @{username or 'بدون معرف'}</b>\n"
        f"👤 المالك: <code>{owner_id}</code>\n"
        f"📌 الحالة: {status}\n"
        f"👥 المحادثات والقروبات النشطة: {chats[0]}\n"
        f"💬 عدد رسائل النشر: {msgs[0]}\n"
        f"🕒 التاريخ: {created_at}"
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("deletebot:"))
async def delete_bot(call: CallbackQuery):
    await call.answer("تم حذف البوت.")
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
    await list_bots(call)

@router.callback_query(F.data == "messages_menu")
async def messages_menu(call: CallbackQuery):
    await call.answer()
    b = InlineKeyboardBuilder()
    b.button(text="➕ إضافة رسالة نشر بفاصل زمني", callback_data="add_message_select")
    b.button(text="📋 قائمة الرسائل المضافة", callback_data="saved_messages_select")
    b.button(text="⬅️ رجوع", callback_data="home")
    b.adjust(1)
    await call.message.edit_text("📤 <b>إدارة النشر التلقائي</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "add_message_select")
async def add_message_select(call: CallbackQuery):
    await call.answer()
    rows = await db_execute("SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC", fetch=True)
    b = InlineKeyboardBuilder()
    for bot_id, username in rows:
        b.button(text=f"🤖 @{username or 'بدون معرف'}", callback_data=f"addmsg:{bot_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text("اختر البوت لتسجيل رسالة جديدة له:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("addmsg:"))
async def addmsg_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    bot_db_id = int(call.data.split(":")[1])
    await state.set_state(AddMessageState.text)
    await state.update_data(bot_id=bot_db_id)
    await call.message.edit_text("📝 أرسل نص الرسالة التي سيتم نشرها:", reply_markup=cancel_keyboard())

@router.message(AddMessageState.text)
async def addmsg_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text or "")
    await state.set_state(AddMessageState.delay)
    await message.answer("⏱ أرسل الآن الفاصل الزمني بالثواني المخصص لهذه الرسالة (مثال: 60 لإرسالها كل دقيقة):")

@router.message(AddMessageState.delay)
async def addmsg_delay(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        delay = int(message.text.strip())
    except ValueError:
        await message.answer("❌ أرسل رقم صحيح بالثواني.")
        return

    await db_execute("INSERT INTO messages(bot_id, text, delay_seconds, created_at) VALUES(?,?,?,?)", (data["bot_id"], data["text"], delay, await now()))
    await state.clear()
    await message.answer("✅ تم حفظ الرسالة وضبط وقت إعادتها بنجاح!", reply_markup=admin_keyboard())

@router.callback_query(F.data == "saved_messages_select")
async def saved_messages_select(call: CallbackQuery):
    await call.answer()
    rows = await db_execute("SELECT id, username FROM bots WHERE status='active' ORDER BY id DESC", fetch=True)
    b = InlineKeyboardBuilder()
    for bot_id, username in rows:
        b.button(text=f"🤖 @{username or 'بدون معرف'}", callback_data=f"savedmsg:{bot_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text("اختر البوت لعرض رسائله المجدولة:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("savedmsg:"))
async def saved_messages(call: CallbackQuery):
    await call.answer()
    bot_db_id = int(call.data.split(":")[1])
    rows = await db_execute("SELECT id, text, delay_seconds FROM messages WHERE bot_id=? ORDER BY id", (bot_db_id,), fetch=True)
    b = InlineKeyboardBuilder()
    text = "📋 <b>رسائل النشر المجدولة لهذا البوت:</b>\n\n"
    if not rows:
        text += "لا توجد رسائل لهذا البوت."
    else:
        for mid, msg_text, delay in rows:
            preview = (msg_text or "").replace("\n", " ")[:30]
            text += f"🆔 #{mid} | ⏱ كل {delay} ثانية | {preview}\n"
            b.button(text=f"🗑 حذف الرسالة #{mid}", callback_data=f"delmsg:{mid}:{bot_db_id}")
    b.button(text="⬅️ رجوع", callback_data="messages_menu")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("delmsg:"))
async def delete_message(call: CallbackQuery):
    await call.answer("تم حذف الرسالة.")
    _, mid, bot_db_id = call.data.split(":")
    await db_execute("DELETE FROM messages WHERE id=? AND bot_id=?", (int(mid), int(bot_db_id)))
    await saved_messages(call)

@router.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    await call.answer()
    bots = await db_execute("SELECT COUNT(*) FROM bots", fetchone=True)
    active_chats = await db_execute("SELECT COUNT(*) FROM chats WHERE status='active'", fetchone=True)
    total_msgs = await db_execute("SELECT COUNT(*) FROM messages", fetchone=True)

    text = (
        "📊 <b>إحصائيات المصنع الشاملة</b>\n\n"
        f"🤖 عدد البوتات المصنوعة: {bots[0]}\n"
        f"👥 القروبات والمحادثات المكتشفة: {active_chats[0]}\n"
        f"💬 إجمالي رسائل النشر: {total_msgs[0]}\n"
    )
    b = InlineKeyboardBuilder()
    b.button(text="🔄 تحديث", callback_data="stats")
    b.button(text="⬅️ رجوع", callback_data="home")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")

async def main():
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
