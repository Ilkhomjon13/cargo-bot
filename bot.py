# -*- coding: utf-8 -*-
# Namangan Cargo Bot — aiogram 3.22.0
# Rolllar: Mijoz, Ҳайдовчи, Админ
# DB: SQLite (cargoN.db)

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
# SETTINGS (o'zgartiring kerak bo'lsa)
# =======================
BOT_TOKEN = "7370665741:AAEbYoKM5_S2XLDGLqO2re8hnPeAUhjSF7g"
ADMIN_IDS = {1262207928, 2055044676}
DB_FILE = "cargoN.db"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# =======================
# DATABASE
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
            commission INTEGER,
            creator_role TEXT DEFAULT 'customer' -- who created: customer/driver
        )""")
        # drivers
        cur.execute("""
        CREATE TABLE IF NOT EXISTS drivers(
            driver_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            balance REAL DEFAULT 0,
            status TEXT DEFAULT 'active' -- active / blocked
        )""")
        # customers
        cur.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            status TEXT DEFAULT 'active' -- active / blocked
        )""")
        conn.commit()

# =======================
# KEYBOARDS
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
            [KeyboardButton(text="📝 Янгидан буюртма")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def driver_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Янгидан буюртма")],
            [KeyboardButton(text="📜 Бўш буюртмалар")],
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📞 Админ билан боғланиш")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def admin_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Барча буюртмалар")],
            [KeyboardButton(text="🚖 Ҳайдовчилар"), KeyboardButton(text="👥 Мижозлар")],
            [KeyboardButton(text="💵 Баланс тўлдириш"), KeyboardButton(text="🔒 Блок/Блокдан чиқариш")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def phone_request_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]], resize_keyboard=True)

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

class AdminTopUpData(StatesGroup):
    target_driver = State()
    custom_amount = State()

# =======================
# HELPERS
# =======================
def list_active_driver_ids() -> list[int]:
    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id FROM drivers WHERE status='active'").fetchall()
        return [r["driver_id"] for r in rows]

async def push_new_order_to_drivers(order_row: sqlite3.Row):
    """Admin комиссия белгилагандан кейин — барча active haydovchilarga push."""
    fee = order_row["commission"] or 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{order_row['id']}"),
        InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{order_row['id']}")
    ]])
    text = (
        f"📢 <b>Янги буюртма!</b>\n\n"
        f"🆔 {order_row['id']} | {order_row['date']}\n"
        f"📍 {order_row['from_address']} ➜ {order_row['to_address']}\n"
        f"📦 {order_row['cargo_type']}\n"
        f"🚘 {order_row['car_type']}\n"
        f"⚖️ {order_row['cargo_weight']} кг\n"
        f"💵 Комиссия: <b>{fee}</b> сўм\n\n"
        f"Биринчи бўлиб қабул қилган ҳайдовчига бириктирилади."
    )
    for did in list_active_driver_ids():
        try:
            await bot.send_message(did, text, reply_markup=kb)
        except Exception:
            pass

def format_order_row(r: sqlite3.Row) -> str:
    """Chiroyli holda buyurtma matnini qaytaradi (ustunlarga bo'lingan)."""
    fee = r["commission"] if r["commission"] is not None else "—"
    driver_line = f"🚖 Haydovchi: {r['driver_id']}" if r["driver_id"] else "🚖 Haydovchi: —"
    username = r["customer_username"] or "—"
    phone = r["customer_phone"] or "—"
    return (
        f"🆔 {r['id']} | {r['date']}\n"
        f"      {r['from_address']} ➜ {r['to_address']}\n"
        f"📦 {r['cargo_type']}\n"
        f"🚘 {r['car_type']}\n"
        f"⚖️ {r['cargo_weight']} кг\n"
        f"📊 Холат: {r['status']}\n"
        f"💸 Комиссия: {fee}\n"
        f"👤 {username}\n"
        f"📞 {phone}\n"
        f"{driver_line}"
    )

async def top_up_balance_and_notify(driver_id: int, amount: int):
    """Bazani yangilab, haydovchiga push yuboradi."""
    with closing(db()) as conn, conn:
        conn.execute("UPDATE drivers SET balance = balance + ? WHERE driver_id=?", (amount, driver_id))
        conn.commit()
        new_bal = conn.execute("SELECT balance FROM drivers WHERE driver_id=?", (driver_id,)).fetchone()
        new_bal_value = int(new_bal["balance"]) if new_bal else amount
    # push
    try:
        await bot.send_message(
            driver_id,
            f"💳 <b>Balansingiz to‘ldirildi!</b>\n\n"
            f"Sizga +<b>{amount}</b> сўм қўшилди ✅\n"
            f"📊 Жорий баланс: <b>{new_bal_value}</b> сўм",
        )
    except Exception:
        pass

# =======================
# START
# =======================
@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMIN_IDS:
        await message.answer("<b>👑 Админ интерфейс</b>\n\nАдмин менюдан бирор бўлимни танланг:", reply_markup=admin_menu_kb())
        return

    with closing(db()) as conn:
        driver = conn.execute("SELECT * FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
        customer = conn.execute("SELECT * FROM customers WHERE user_id=?", (message.from_user.id,)).fetchone()

    if driver:
        if driver["status"] == "blocked":
            await message.answer("❗ Сиз ҳозирча блоклангансиз. Илтимос админ билан боғланинг.")
            return
        await message.answer("👋 Салом, ҳайдовчи!", reply_markup=driver_menu_kb())
        return

    await message.answer("👋 Салом! Илтимос, ролингизни танланг:", reply_markup=role_kb())

# =======================
# ROLE CHOICE
# =======================
@router.message(F.text == "👤 Мижоз")
async def role_customer(message: Message):
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO customers(user_id, username, phone, status) VALUES(?,?,?,?)",
                     (message.from_user.id, f"@{message.from_user.username}" if message.from_user.username else None, None, "active"))
    await message.answer("✅ Сиз мижоз сифатида рўйхатдан ўтдингиз!", reply_markup=customer_menu_kb())

@router.message(F.text == "🚖 Ҳайдовчи")
async def role_driver(message: Message, state: FSMContext):
    with closing(db()) as conn:
        drv = conn.execute("SELECT status FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if drv and drv["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз. Админга мурожаат қилинг.")
        return
    await state.set_state(DriverRegistration.ask_phone)
    await message.answer("📱 Илтимос, телефон рақамингизни юборинг:", reply_markup=phone_request_kb())

@router.message(DriverRegistration.ask_phone, F.contact)
async def driver_save_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    uname = message.from_user.username
    with closing(db()) as conn:
        old = conn.execute("SELECT driver_id FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()

    with closing(db()) as conn, conn:
        if old is None:
            conn.execute(
                "INSERT INTO drivers(driver_id, username, phone, balance, status) VALUES(?,?,?,?,?)",
                (message.from_user.id, f"@{uname}" if uname else None, phone, 99000, "active")
            )
            await message.answer("🎉 Янги рўйхатдан ўтганингиз учун балансингизга бонус сифатида <b>99 000</b> сўм тўлдирилди!")
            # notify admins about new driver
            for aid in ADMIN_IDS:
                try:
                    await bot.send_message(aid, f"🚨 Янги ҳайдовчи рўйхатдан ўтди: @{uname or message.from_user.id} | ID: {message.from_user.id}")
                except Exception:
                    pass
        else:
            conn.execute(
                "UPDATE drivers SET username=?, phone=? WHERE driver_id=?",
                (f"@{uname}" if uname else None, phone, message.from_user.id)
            )

    await state.clear()
    await message.answer("✅ Рўйхатдан ўтдингиз!", reply_markup=driver_menu_kb())

# =======================
# NEW ORDER (customer and driver both can)
# =======================
@router.message(F.text == "📝 Янгидан буюртма")
async def new_order(message: Message, state: FSMContext):
    # check blocked
    with closing(db()) as conn:
        cust = conn.execute("SELECT status FROM customers WHERE user_id=?", (message.from_user.id,)).fetchone()
        drv = conn.execute("SELECT status FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if cust and cust["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз ва буюртма бера olmaysiz. Админ билан боғланинг.")
        return
    if drv and drv["status"] == "blocked":
        await message.answer("❗ Сиз блокланган ҳайдовчисиз. Админга мурожаат қилинг.")
        return

    # who creates it?
    creator_role = "driver" if drv else "customer"
    await state.update_data(creator_role=creator_role)
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
        # decide menu
        with closing(db()) as conn:
            drv = conn.execute("SELECT * FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
        await message.answer("❌ Буюртма бекор қилинди.", reply_markup=driver_menu_kb() if drv else customer_menu_kb())
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
    creator_role = data.get("creator_role", "customer")

    with closing(db()) as conn, conn:
        conn.execute("""
            INSERT INTO orders(
                customer_id, from_address, to_address, cargo_type, car_type, cargo_weight, date,
                status, customer_username, customer_phone, creator_role
            )
            VALUES(?,?,?,?,?,?,?, 'pending_fee', ?, ?, ?)
        """, (
            message.from_user.id,
            data["from_address"], data["to_address"],
            data["cargo_type"], data["car_type"],
            data["cargo_weight"], now,
            customer_username, phone, creator_role
        ))
        order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        # upsert customer info
        conn.execute("INSERT OR REPLACE INTO customers(user_id, username, phone, status) VALUES(?,?,?, COALESCE((SELECT status FROM customers WHERE user_id=?), 'active'))",
                     (message.from_user.id, customer_username, phone, message.from_user.id))

    await state.clear()
    await message.answer(
        f"✅ Буюртмангиз #{order_id} қабул қилинди!\nАдмин томонидан комиссия белгилангандан сўнг ҳайдовчиларга юборатилади.",
        reply_markup=driver_menu_kb() if creator_role=="driver" else customer_menu_kb()
    )

    # notify admins to set commission
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

    text_admin = (
        f"🆕 <b>Янги буюртма (комиссия кутилаяпти)</b>\n\n"
        f"🆔 {order['id']} | {order['date']}\n"
        f"📍 {order['from_address']} ➜ {order['to_address']}\n"
        f"📦 {order['cargo_type']}\n"
        f"🚘 {order['car_type']}\n"
        f"⚖️ {order['cargo_weight']} кг\n"
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
# ADMIN: set commission
# =======================
@router.callback_query(F.data.startswith("setfee:"))
async def set_fee(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True); return
    try:
        _, oid, fee = callback.data.split(":")
        order_id = int(oid); fee = int(fee)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True); return

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

    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await push_new_order_to_drivers(order)

# =======================
# DRIVER: free orders (and accept/reject)
# =======================
@router.message(F.text == "📜 Бўш буюртмалар")
async def free_orders(message: Message):
    with closing(db()) as conn:
        d = conn.execute("SELECT status FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if not d:
        await message.answer("❌ Ҳайдовчи сифатида рўйхатдан ўтинг.", reply_markup=role_kb()); return
    if d["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз. Админ билан боғланинг."); return

    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await message.answer("📭 Ҳозирча бўш буюртма йўқ."); return
    for r in rows:
        fee = r["commission"] or 0
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{r['id']}"),
             InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{r['id']}")]
        ])
        text = format_order_row(r)
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("accept:"))
async def accept_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        d = conn.execute("SELECT balance, phone, username, status FROM drivers WHERE driver_id=?", (callback.from_user.id,)).fetchone()
        if not d:
            await callback.answer("Ҳайдовчи сифатида рўйхатдан ўтинг.", show_alert=True); return
        if d["status"] == "blocked":
            await callback.answer("❗ Сиз блоклангансиз. Админ билан боғланинг.", show_alert=True); return
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

    if not order or order["status"] != "open":
        await callback.answer("❌ Буюртма қолмаган ёки аллақачон олинган.", show_alert=True); return

    fee = int(order["commission"] or 0)
    if (d["balance"] or 0) < fee:
        await callback.answer(f"❌ Балансингиз етарли эмас. Керак: {fee} сўм.", show_alert=True); return

    with closing(db()) as conn, conn:
        row = conn.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row or row["status"] != "open":
            await callback.answer("❌ Кечикдингиз, буюртма банд бўлди.", show_alert=True); return
        conn.execute("UPDATE orders SET status='taken', driver_id=? WHERE id=? AND status='open'", (callback.from_user.id, order_id))
        conn.execute("UPDATE drivers SET balance = balance - ? WHERE driver_id=?", (fee, callback.from_user.id))
        conn.commit()

    await callback.answer("✅ Буюртма қабул қилинди!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # send details to driver and customer
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        d_info = conn.execute("SELECT username, phone FROM drivers WHERE driver_id=?", (callback.from_user.id,)).fetchone()
    phone_line = f"📞 Телефон: <b>{order['customer_phone']}</b>\n" if order["customer_phone"] else ""
    username_line = f"👤 Telegram: <b>{order['customer_username']}</b>\n"
    complete_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить заказ / Буюртмани якунлаш", callback_data=f"complete:{order_id}")]
    ])
    await bot.send_message(
        callback.from_user.id,
        "🚚 Буюртма маълумотлари (ҳайдовчи учун):\n" + format_order_row(order),
        reply_markup=complete_kb
    )

    # notify customer
    driver_username = d_info["username"] or f"id:{callback.from_user.id}"
    driver_phone = d_info["phone"] or "телефон не указан"
    try:
        await bot.send_message(order["customer_id"],
                               "✅ Сизнинг буюртмангиз ҳайдовчи томонидан қабул қилинди!\n"
                               f"👤 Водитель: <b>{driver_username}</b>\n"
                               f"📞 Телефон: <b>{driver_phone}</b>\n"
                               f"🚚 Буюртма раками: #{order_id}")
    except Exception:
        pass

@router.callback_query(F.data.startswith("complete:"))
async def complete_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            await callback.answer("❌ Буюртма топилмади.", show_alert=True); return
        if order["driver_id"] != callback.from_user.id:
            await callback.answer("❌ Фақат ушбу буюртмани олиб бормоқчи бўлган ҳайдовчи якунлай олади.", show_alert=True); return
        if order["status"] != "taken":
            await callback.answer("❌ Буюртма якунланмаган ёки ҳолати мувофиқ эмас.", show_alert=True); return
        driver = conn.execute("SELECT username, phone FROM drivers WHERE driver_id=?", (callback.from_user.id,)).fetchone()
        conn.execute("UPDATE orders SET status='done' WHERE id=?", (order_id,))
        conn.commit()

    await callback.answer("✅ Буюртма якунланди!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    driver_username = driver["username"] or f"id:{callback.from_user.id}"
    driver_phone = driver["phone"] or "телефон не указан"
    try:
        await bot.send_message(order["customer_id"],
                               f"🚚 Сизнинг буюртмангиз #{order_id} муваффақиятли тугалланди.\n"
                               f"👤 Водитель: <b>{driver_username}</b>\n"
                               f"📞 Телефон: <b>{driver_phone}</b>")
    except Exception:
        pass

    await callback.message.answer(f"✅ Буюртма #{order_id} якунланди.", reply_markup=driver_menu_kb())

@router.callback_query(F.data.startswith("reject:"))
async def reject_order(callback: CallbackQuery):
    await callback.answer("❌ Сиз рад этдингиз.")

# =======================
# BALANCE (driver)
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
    admins = ", ".join([f"<a href='tg://user?id={aid}'>@zaaaza13</a>" for aid in ADMIN_IDS])
    await message.answer(f"📞 Админлар билан боғланиш: {@mirzayev707}", disable_web_page_preview=True)

# =======================
# ADMIN: lists, block/unblock, balance topup
# =======================
@router.message(F.text == "📊 Барча буюртмалар")
async def all_orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 50").fetchall()
    if not rows:
        await message.answer("📭 Буюртмалар йўқ."); return
    for r in rows:
        await message.answer(format_order_row(r))

@router.message(F.text == "🚖 Ҳайдовчилар")
async def list_drivers_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM drivers ORDER BY driver_id DESC").fetchall()
    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ."); return
    for r in rows:
        status = r["status"] or "active"
        text = f"🆔 {r['driver_id']} | {r['username'] or '—'} | 📞 {r['phone'] or '—'} | 💰 {int(r['balance'] or 0)} сўм | Статус: <b>{status}</b>"
        if status == "active":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 Блоклаш", callback_data=f"drv_block:{r['driver_id']}")]])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Блокдан чиқариш", callback_data=f"drv_unblock:{r['driver_id']}")]])
        await message.answer(text, reply_markup=kb)

@router.message(F.text == "👥 Мижозлар")
async def list_customers_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY user_id DESC").fetchall()
    if not rows:
        await message.answer("📭 Мижозлар йўқ."); return
    for r in rows:
        status = r["status"] or "active"
        text = f"🆔 {r['user_id']} | {r['username'] or '—'} | 📞 {r['phone'] or '—'} | Статус: <b>{status}</b>"
        if status == "active":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 Блоклаш", callback_data=f"cust_block:{r['user_id']}")]])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Блокдан чиқариш", callback_data=f"cust_unblock:{r['user_id']}")]])
        await message.answer(text, reply_markup=kb)

# block/unblock callbacks
@router.callback_query(F.data.startswith("drv_block:"))
async def drv_block(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    driver_id = int(callback.data.split(":")[1])
    with closing(db()) as conn, conn:
        conn.execute("UPDATE drivers SET status='blocked' WHERE driver_id=?", (driver_id,))
    await callback.answer(f"🔒 {driver_id} блокланди.", show_alert=True)
    try:
        await bot.send_message(driver_id, "🚫 Сиз админ томонидан блокландингиз. Илтимос админ билан боғланинг.")
    except Exception:
        pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("drv_unblock:"))
async def drv_unblock(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    driver_id = int(callback.data.split(":")[1])
    with closing(db()) as conn, conn:
        conn.execute("UPDATE drivers SET status='active' WHERE driver_id=?", (driver_id,))
    await callback.answer(f"✅ {driver_id} блокдан чиқарилди.", show_alert=True)
    try:
        await bot.send_message(driver_id, "✅ Сиз блокдан чиқарилдингиз. Ботдан фойдаланишингиз мумкин.")
    except Exception:
        pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("cust_block:"))
async def cust_block(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    user_id = int(callback.data.split(":")[1])
    with closing(db()) as conn, conn:
        conn.execute("UPDATE customers SET status='blocked' WHERE user_id=?", (user_id,))
    await callback.answer(f"🔒 Мижоз {user_id} блокланди.", show_alert=True)
    try:
        await bot.send_message(user_id, "🚫 Сизни админ томонидан блоклашди. Илтимос админ билан боғланинг.")
    except Exception:
        pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("cust_unblock:"))
async def cust_unblock(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    user_id = int(callback.data.split(":")[1])
    with closing(db()) as conn, conn:
        conn.execute("UPDATE customers SET status='active' WHERE user_id=?", (user_id,))
    await callback.answer(f"✅ Мижоз {user_id} блокдан чиқарилди.", show_alert=True)
    try:
        await bot.send_message(user_id, "✅ Сиз блокдан чиқарилдингиз. Ботдан фойдаланишингиз мумкин.")
    except Exception:
        pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# =======================
# ADMIN: BALANCE TOPUP (with push)
# =======================
@router.message(F.text == "💵 Баланс тўлдириш")
async def admin_topup_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id, username FROM drivers").fetchall()
    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ."); return
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
        await callback.answer("Ички хатолик."); return

    if choice == "other":
        await state.update_data(target_driver=driver_id)
        await state.set_state(AdminTopUpData.custom_amount)
        await callback.message.answer("✍️ Суммани киритинг (сўм, бутун рақам):")
        await callback.answer()
        return

    amount = int(choice)
    await state.clear()
    await top_up_balance_and_notify(driver_id, amount)
    await callback.message.answer(f"✅ Баланс {driver_id} учун <b>{amount}</b> сўмга тўлдирилди.")
    await callback.answer()

@router.message(AdminTopUpData.custom_amount, F.text)
async def topup_custom_amount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("❗ Фақат бутун сони киритинг (масалан, 75000)."); return
    amount = int(txt)
    data = await state.get_data()
    driver_id = data.get("driver_id") or data.get("target_driver")
    if driver_id is None:
        await message.answer("Ички хатолик."); return
    await state.clear()
    await top_up_balance_and_notify(driver_id, amount)
    await message.answer(f"✅ Баланс {driver_id} учун <b>{amount}</b> сўмга тўлдирилди.", reply_markup=admin_menu_kb())

# =======================
# HOME BUTTON
# =======================
@router.message(F.text == "🏠 Бош меню")
async def go_home(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("👑 Админ меню:", reply_markup=admin_menu_kb()); return
    with closing(db()) as conn:
        drv = conn.execute("SELECT * FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if drv:
        await message.answer("👋 Салом, ҳайдовчи!", reply_markup=driver_menu_kb())
    else:
        await message.answer("👋 Салом!", reply_markup=customer_menu_kb())

# =======================
# MAIN
# =======================
async def main():
    init_db()
    print("🚀 Bot ишга тушди...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
