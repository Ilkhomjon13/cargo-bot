# -*- coding: utf-8 -*-
# Namangan Cargo Bot — aiogram 3.22.0
# Роллар: Мижоз, Ҳайдовчи, Админ
# База: SQLite
# Yangiliklar:
#  - Buyurtma avval adminlarga boradi: комиссия 5000/10000/15000 tugmalaridan tanlanadi
#  - Комиссия orders.commission maydonida, admin tasdiqlagach haydovchilarga push
#  - Haydovchi qabul qilganda balansdan komissiya yechiladi (yetmasa qabul qila olmaydi)
#  - Yangi ro‘yxatdan o‘tgan haydovchiga 99 000 so‘m bonus
#  - Mijozdan telefon so‘raladi; haydovchi qabul qilgach, username + telefon beriladi

import os
import asyncio
import sqlite3
import re
from contextlib import closing
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime

# =======================
# НАСТРОЙКИ
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {1262207928, 555555555}

DB_FILE = "cargo.db"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# =======================
# БАЗА ДАННЫХ
# =======================
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def _column_exists(conn, table: str, col: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == col for c in cols)

def init_db():
    with closing(db()) as conn, conn:
        cur = conn.cursor()
        # orders
        cur.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            from_address TEXT,
            to_address TEXT,
            cargo_type TEXT,
            car_type TEXT,
            cargo_weight REAL,
            date TEXT,
            status TEXT CHECK(status IN ('pending_fee','open','taken','done')) DEFAULT 'pending_fee',
            driver_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            customer_username TEXT,
            customer_phone TEXT,
            commission INTEGER
        )""")
        # ensure columns exist
        for col, coldef in [
            ("commission", "INTEGER"),
            ("status", "TEXT DEFAULT 'pending_fee'"),
            ("customer_phone", "TEXT"),
            ("customer_username", "TEXT"),
            ("car_type", "TEXT"),
        ]:
            if not _column_exists(conn, "orders", col):
                conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {coldef}")

        # drivers
        cur.execute("""
        CREATE TABLE IF NOT EXISTS drivers(
            driver_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            balance REAL DEFAULT 0
        )""")
        if not _column_exists(conn, "drivers", "balance"):
            conn.execute("ALTER TABLE drivers ADD COLUMN balance REAL DEFAULT 0")

# =======================
# КЛАВИАТУРАЛАР
# =======================
def role_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚖 Ҳайдовчи"), KeyboardButton(text="👤 Мижоз")]
        ],
        resize_keyboard=True
    )

def car_type_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚐 Лабо"), KeyboardButton(text="🚛 Бонго")],
            [KeyboardButton(text="🚚 Исузи")],
            [KeyboardButton(text="⬅️ Бекор қилиш")]
        ],
        resize_keyboard=True
    )

def customer_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Янгидан буюртма")]
        ],
        resize_keyboard=True
    )

def driver_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 Бўш буюртмалар")],
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📞 Админ билан боғланиш")]
        ],
        resize_keyboard=True
    )

def admin_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Барча буюртмалар")],
            [KeyboardButton(text="🚖 Ҳайдовчилар")],
            [KeyboardButton(text="💵 Баланс тўлдириш")]
        ],
        resize_keyboard=True
    )

def phone_request_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]],
        resize_keyboard=True
    )

def topup_amount_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 000", callback_data="adm_topup_amt:5000"),
         InlineKeyboardButton(text="10 000", callback_data="adm_topup_amt:10000"),
         InlineKeyboardButton(text="15 000", callback_data="adm_topup_amt:15000")],
        [InlineKeyboardButton(text="✍️ Бошқа сумма", callback_data="adm_topup_amt:other")]
    ])

def commission_kb(order_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 000", callback_data=f"setfee:{order_id}:5000"),
         InlineKeyboardButton(text="10 000", callback_data=f"setfee:{order_id}:10000"),
         InlineKeyboardButton(text="15 000", callback_data=f"setfee:{order_id}:15000")]
    ])

# =======================
# FSM
# =======================
class NewOrder(StatesGroup):
    from_address = State()
    to_address = State()
    cargo_type = State()
    car_type = State()
    cargo_weight = State()
    ask_phone = State()

class DriverRegistration(StatesGroup):
    ask_phone = State()

class BalanceTopUp(StatesGroup):
    choose_driver = State()
    enter_amount = State()

class AdminTopUpData(StatesGroup):
    # ichki yordamchi holat (driver_id saqlash)
    target_driver = State()
    custom_amount = State()

# =======================
# YORDAMCHI FUNKSIYALAR
# =======================
def list_driver_ids() -> list[int]:
    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id FROM drivers").fetchall()
        return [r["driver_id"] for r in rows]

async def push_new_order_to_drivers(order_row: sqlite3.Row):
    """Admin комиссия белгилагандан кейин — barcha haydovchilarga push."""
    fee = order_row["commission"] or 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{order_row['id']}"),
        InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{order_row['id']}")
    ]])
    text = (
        f"📢 <b>Янги буюртма!</b>\n\n"
        f"🆔 {order_row['id']} | {order_row['date']}\n"
        f"📍 {order_row['from_address']} ➜ {order_row['to_address']}\n"
        f"📦 {order_row['cargo_type']} | 🚘 {order_row['car_type']} | ⚖️ {order_row['cargo_weight']} кг\n"
        f"💵 Комиссия: <b>{fee}</b> сўм\n\n"
        f"Биринчи бўлиб қабул қилган ҳайдовчига бириктирилади."
    )
    for did in list_driver_ids():
        try:
            await bot.send_message(did, text, reply_markup=kb)
        except Exception:
            pass

# =======================
# СТАРТ
# =======================
@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMIN_IDS:
        await message.answer("👋 Сиз админ сифатида кирдингиз!", reply_markup=admin_menu_kb())
        return

    with closing(db()) as conn:
        driver = conn.execute("SELECT * FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()

    if driver:
        await message.answer("👋 Салом, ҳайдовчи!", reply_markup=driver_menu_kb())
    else:
        await message.answer("👋 Салом! Илтимос, ролингизни танланг:", reply_markup=role_kb())

# =======================
# РОЛ ТАНЛАШ
# =======================
@router.message(F.text == "👤 Мижоз")
async def role_customer(message: Message):
    await message.answer("✅ Сиз мижоз сифатида рўйхатдан ўтдингиз!", reply_markup=customer_menu_kb())

@router.message(F.text == "🚖 Ҳайдовчи")
async def role_driver(message: Message, state: FSMContext):
    await state.set_state(DriverRegistration.ask_phone)
    await message.answer("📱 Илтимос, телефон рақамингизни юборинг:", reply_markup=phone_request_kb())

@router.message(DriverRegistration.ask_phone, F.contact)
async def driver_save_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    uname = message.from_user.username
    with closing(db()) as conn:
        old = conn.execute("SELECT driver_id FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()

    with closing(db()) as conn, conn:
        # yangi haydovchi bo'lsa — 99 000 bonus
        if old is None:
            conn.execute(
                "INSERT INTO drivers(driver_id, username, phone, balance) VALUES(?,?,?,?)",
                (message.from_user.id, f"@{uname}" if uname else None, phone, 99000)
            )
            await message.answer("🎉 Янги рўйхатдан ўтганингиз учун балансингизга бонус сифатида <b>99 000</b> сўм тўлдирилди!")
        else:
            conn.execute(
                "UPDATE drivers SET username=?, phone=? WHERE driver_id=?",
                (f"@{uname}" if uname else None, phone, message.from_user.id)
            )

    await state.clear()
    await message.answer("✅ Рўйхатдан ўтдингиз!", reply_markup=driver_menu_kb())

# =======================
# МИЖОЗ БУЮРТМА (сана/вақт — авто)
# =======================
@router.message(F.text == "📝 Янгидан буюртма")
async def new_order(message: Message, state: FSMContext):
    await state.set_state(NewOrder.from_address)
    await message.answer("📍 Қаердан юк олинади? Манзилни киритинг:")

@router.message(NewOrder.from_address, F.text)
async def order_from(message: Message, state: FSMContext):
    await state.update_data(from_address=message.text.strip())
    await state.set_state(NewOrder.to_address)
    await message.answer("📍 Қаерга юборилади? Манзилни киритинг:")

@router.message(NewOrder.to_address, F.text)
async def order_to(message: Message, state: FSMContext):
    await state.update_data(to_address=message.text.strip())
    await state.set_state(NewOrder.cargo_type)
    await message.answer("📦 Юк турини киритинг:")

@router.message(NewOrder.cargo_type, F.text)
async def order_cargo(message: Message, state: FSMContext):
    await state.update_data(cargo_type=message.text.strip())
    await state.set_state(NewOrder.car_type)
    await message.answer("🚘 Қайси машина керак? Тугмалардан танланг:", reply_markup=car_type_kb())

@router.message(NewOrder.car_type, F.text)
async def order_car(message: Message, state: FSMContext):
    if message.text not in ["🚐 Лабо", "🚛 Бонго", "🚚 Исузи", "⬅️ Бекор қилиш"]:
        await message.answer("❌ Илтимос, тугмалардан танланг.")
        return
    if message.text == "⬅️ Бекор қилиш":
        await state.clear()
        await message.answer("❌ Буюртма бекор қилинди.", reply_markup=customer_menu_kb())
        return
    await state.update_data(car_type=message.text)
    await state.set_state(NewOrder.cargo_weight)
    await message.answer("⚖️ Юк оғирлигини киритинг (кг):", reply_markup=ReplyKeyboardMarkup(keyboard=[[]], resize_keyboard=True))

@router.message(NewOrder.cargo_weight, F.text)
async def order_weight(message: Message, state: FSMContext):
    val = message.text.strip().replace(",", ".")
    if not re.match(r"^\d+(\.\d+)?$", val):
        await message.answer("❌ Фақат рақам киритинг (масалан: 150 ёки 75.5).")
        return
    await state.update_data(cargo_weight=float(val))
    # mijoz telefoni so'raladi
    await state.set_state(NewOrder.ask_phone)
    await message.answer("📱 Телефон рақамингизни юборинг ёки тугмадан фойдаланинг:", reply_markup=phone_request_kb())

@router.message(NewOrder.ask_phone)
async def order_phone(message: Message, state: FSMContext):
    phone = None
    if getattr(message, "contact", None):
        if not message.contact.user_id or message.contact.user_id == message.from_user.id:
            phone = message.contact.phone_number
    if not phone and message.text:
        t = message.text.strip()
        if re.match(r"^\+?\d[\d\s\-\(\)]{7,}$", t):
            phone = t

    if not phone:
        await message.answer("❗ Телефон рақамини тўғри юборинг ёки «📱 Телефон рақамни юбориш» тугмасидан фойдаланинг.")
        return

    data = await state.get_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    uname = message.from_user.username
    customer_username = f"@{uname}" if uname else f"id:{message.from_user.id}"

    with closing(db()) as conn, conn:
        conn.execute("""
            INSERT INTO orders(
                customer_id, from_address, to_address, cargo_type, car_type, cargo_weight, date,
                status, customer_username, customer_phone
            )
            VALUES(?,?,?,?,?,?,?, 'pending_fee', ?, ?)
        """, (
            message.from_user.id,
            data["from_address"], data["to_address"],
            data["cargo_type"], data["car_type"],
            data["cargo_weight"], now,
            customer_username, phone
        ))
        order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    await state.clear()
    await message.answer(
        f"✅ Буюртмангиз #{order_id} қабул қилинди!\n"
        f"Админ томонидан комиссия белгилангандан сўнг ҳайдовчиларга юборатилади.",
        reply_markup=customer_menu_kb()
    )

    # Adminlarga yuborish — комиссияни танlash tugmalari билан
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

    text_admin = (
        f"🆕 <b>Янги буюртма (комиссия кутилаяпти)</b>\n\n"
        f"🆔 {order['id']} | {order['date']}\n"
        f"📍 {order['from_address']} ➜ {order['to_address']}\n"
        f"📦 {order['cargo_type']} | 🚘 {order['car_type']} | ⚖️ {order['cargo_weight']} кг\n"
        f"👤 Мижоз: {order['customer_username']}\n"
        f"📞 Телефон: {order['customer_phone']}\n\n"
        f"Комиссияни танланг:"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text_admin, reply_markup=commission_kb(order_id))
        except Exception:
            pass

# =======================
# ADMIN: комиссияни ўрнатиш
# =======================
@router.callback_query(F.data.startswith("setfee:"))
async def set_fee(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True)
        return
    try:
        _, oid, fee = callback.data.split(":")
        order_id = int(oid)
        fee = int(fee)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True)
        return

    with closing(db()) as conn, conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            await callback.answer("Буюртма топилмади.", show_alert=True); return
        if row["status"] != "pending_fee":
            await callback.answer("Комиссия аллақачон белгиланган.", show_alert=True); return

        conn.execute("UPDATE orders SET commission=?, status='open' WHERE id=?", (fee, order_id))

    await callback.answer("Комиссия ўрнатилди. Ҳайдовчиларга юборилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # haydovchilarga push
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await push_new_order_to_drivers(order)

# =======================
# ҲАЙДОВЧИ: Бўш буюртмалар (қўлда кўриш)
# =======================
@router.message(F.text == "📜 Бўш буюртмалар")
async def free_orders(message: Message):
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await message.answer("📭 Ҳозирча бўш буюртма йўқ.")
        return
    for r in rows:
        fee = r["commission"] or 0
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{r['id']}"),
             InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{r['id']}")]
        ])
        text = (f"🆔 {r['id']} | {r['date']}\n"
                f"📍 {r['from_address']} ➜ {r['to_address']}\n"
                f"📦 {r['cargo_type']} | 🚘 {r['car_type']} | ⚖️ {r['cargo_weight']} кг\n"
                f"💵 Комиссия: <b>{fee}</b> сўм")
        await message.answer(text, reply_markup=kb)

# =======================
# ҚАБУЛ / РАД
# =======================
@router.callback_query(F.data.startswith("accept:"))
async def accept_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    with closing(db()) as conn:
        d = conn.execute("SELECT balance, phone, username FROM drivers WHERE driver_id=?", (callback.from_user.id,)).fetchone()
    if not d:
        await callback.answer("Ҳайдовчи сифатида рўйхатдан ўтинг.", show_alert=True); return

    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "open":
        await callback.answer("❌ Буюртма қолмаган ёки аллақачон олинган.", show_alert=True)
        return

    fee = int(order["commission"] or 0)
    if (d["balance"] or 0) < fee:
        await callback.answer(f"❌ Балансингиз етарли эмас. Керак: {fee} сўм.", show_alert=True)
        return

    # atomik: status tekshirish + update + balance yechish
    with closing(db()) as conn, conn:
        row = conn.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row or row["status"] != "open":
            await callback.answer("❌ Кечикдингиз, буюртма банд бўлди.", show_alert=True); return

        # band qilish
        conn.execute("UPDATE orders SET status='taken', driver_id=? WHERE id=? AND status='open'",
                     (callback.from_user.id, order_id))
        # balansdan yechish
        conn.execute("UPDATE drivers SET balance = balance - ? WHERE driver_id=?", (fee, callback.from_user.id))

    await callback.answer("✅ Буюртма қабул қилинди!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # haydovchiga mijoz kontaktlari
    phone_line = f"📞 Телефон: <b>{order['customer_phone']}</b>\n" if order["customer_phone"] else ""
    username_line = f"👤 Telegram: <b>{order['customer_username']}</b>\n"
    deep_link = f"<a href='tg://user?id={order['customer_id']}'>TG орқали ёзиш</a>"

    await bot.send_message(
        callback.from_user.id,
        "🚚 Буюртма маълумотлари (ҳайдовчи учун):\n"
        f"🆔 #{order_id}\n"
        f"📍 {order['from_address']} ➜ {order['to_address']}\n"
        f"📦 {order['cargo_type']} | 🚘 {order['car_type']} | ⚖️ {order['cargo_weight']} кг\n\n"
        f"{username_line}{phone_line}{deep_link}",
        reply_markup=driver_menu_kb()
    )

    # mijozga xabar
    try:
        await bot.send_message(order["customer_id"], "✅ Сизнинг буюртмангиз ҳайдовчи томонидан қабул қилинди.")
    except Exception:
        pass

@router.callback_query(F.data.startswith("reject:"))
async def reject_order(callback: CallbackQuery):
    await callback.answer("❌ Сиз рад этдингиз.")

# =======================
# БАЛАНС (Ҳайдовчи)
# =======================
@router.message(F.text == "💰 Баланс")
async def driver_balance(message: Message):
    with closing(db()) as conn:
        driver = conn.execute("SELECT balance FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if not driver:
        await message.answer("❌ Баланс топилмади.")
        return
    await message.answer(f"💰 Сизнинг балансингиз: <b>{int(driver['balance'])}</b> сўм")

@router.message(F.text == "📞 Админ билан боғланиш")
async def contact_admin(message: Message):
    # Бу ерга ўз админ юзернейм(лар)ингизни ёзинг
    admins = ", ".join([f"<a href='tg://user?id={aid}'>@zaaaza13</a>" for aid in ADMIN_IDS])
    await message.answer(f"📞 Админлар билан боғланиш: {admins}", disable_web_page_preview=True)

# =======================
# АДМИН: Рўйхатлар
# =======================
@router.message(F.text == "📊 Барча буюртмалар")
async def all_orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 50").fetchall()
    if not rows:
        await message.answer("📭 Буюртмалар йўқ.")
        return
    text = "<b>📊 Охирги 50 буюртма:</b>\n\n"
    for r in rows:
        fee = r["commission"] if r["commission"] is not None else "—"
        text += (f"🆔 {r['id']} | {r['date']} | {r['from_address']} ➜ {r['to_address']}\n"
                 f"📦 {r['cargo_type']} | 🚘 {r['car_type']} | ⚖️ {r['cargo_weight']} кг | Ҳолат: {r['status']} | Комиссия: {fee}\n"
                 f"👤 {r['customer_username']} | 📞 {r['customer_phone']} | Haydovchi: {r['driver_id']}\n\n")
    await message.answer(text)

@router.message(F.text == "🚖 Ҳайдовчилар")
async def list_drivers(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM drivers").fetchall()
    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ.")
        return
    text = "<b>🚖 Ҳайдовчилар:</b>\n\n"
    for r in rows:
        text += f"🆔 {r['driver_id']} | {r['username']} | 📞 {r['phone']} | 💰 {int(r['balance'])} сўм\n"
    await message.answer(text)

# =======================
# АДМИН: Баланс тўлдириш
# =======================
@router.message(F.text == "💵 Баланс тўлдириш")
async def admin_topup_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id, username FROM drivers").fetchall()
    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text=f"{r['username'] or r['driver_id']}",
            callback_data=f"topup:{r['driver_id']}")] for r in rows]
    )
    await state.set_state(BalanceTopUp.choose_driver)
    await message.answer("👤 Қайси ҳайдовчига баланс қўшасиз?", reply_markup=kb)

@router.callback_query(F.data.startswith("topup:"))
async def topup_choose(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    driver_id = int(callback.data.split(":")[1])
    await state.update_data(driver_id=driver_id)
    await callback.message.answer("💵 Миқдорни танланг ёки «✍️ Бошқа сумма»:", reply_markup=topup_amount_kb())
    await state.set_state(AdminTopUpData.target_driver)
    await callback.answer()

@router.callback_query(F.data.startswith("adm_topup_amt:"))
async def topup_amount_choice(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    choice = callback.data.split(":")[1]
    data = await state.get_data()
    driver_id = data.get("driver_id") or data.get("target_driver")
    if driver_id is None:
        # fallback
        await callback.answer("Ички хатолик."); return

    if choice == "other":
        await state.update_data(target_driver=driver_id)
        await state.set_state(AdminTopUpData.custom_amount)
        await callback.message.answer("✍️ Суммани киритинг (сўм, бутун рақам):")
        await callback.answer()
        return

    amount = int(choice)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE drivers SET balance = balance + ? WHERE driver_id=?", (amount, driver_id))
    await state.clear()
    await callback.message.answer(f"✅ Баланс {driver_id} учун <b>{amount}</b> сўмга тўлдирилди.")
    await callback.answer()

@router.message(AdminTopUpData.custom_amount, F.text)
async def topup_custom_amount(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("❗ Фақат бутун сони киритинг (масалан, 75000).")
        return
    amount = int(txt)
    data = await state.get_data()
    driver_id = data.get("driver_id") or data.get("target_driver")
    with closing(db()) as conn, conn:
        conn.execute("UPDATE drivers SET balance = balance + ? WHERE driver_id=?", (amount, driver_id))
    await state.clear()
    await message.answer(f"✅ Баланс {driver_id} учун <b>{amount}</b> сўмга тўлдирилди.", reply_markup=admin_menu_kb())

# =======================
# MAIN
# =======================
@dp.message(commands=["start"])
async def main():
    init_db()
    print("🚀 Bot ишга тушди...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
