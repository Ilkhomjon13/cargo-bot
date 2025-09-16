# -*- coding: utf-8 -*-
"""
cargobot_updated.py
To'liq ishlaydigan bot:
- Haydovchi ro'yxatdan o'tishi (telefon, ism, mashina rusumi tugmalar yoki custom)
- Mijoz buyurtma berishi
- Admin: buyurtmalar, haydovchilar, mijozlar, balans to'ldirish, broadcast, block/unblock
- Kvitansiya (photo) yuklanishi va admin tomonidan tasdiqlanishi
"""
import asyncio
import sqlite3
import re
from contextlib import closing
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --------------------------
# SETTINGS: edit these
# --------------------------
BOT_TOKEN = "7370665741:AAEbYoKM5_S2XLDGLqO2re8hnPeAUhjSF7g"
# add your admin IDs here (integers)
ADMIN_IDS = {1262207928, 8011859232}
DB_FILE = "cargoof.db"

# --------------------------
# BOT INIT
# --------------------------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --------------------------
# DATABASE HELPERS
# --------------------------
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(db()) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS drivers(
            driver_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            full_name TEXT,
            car_model TEXT,
            balance REAL DEFAULT 0,
            status TEXT DEFAULT 'active'
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            full_name TEXT,
            status TEXT DEFAULT 'active'
        )""")
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
            status TEXT DEFAULT 'pending_fee', -- pending_fee/open/taken/done
            driver_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            customer_username TEXT,
            customer_phone TEXT,
            commission INTEGER,
            creator_role TEXT DEFAULT 'customer'
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER,
            file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
def add_full_name_column():
    conn = sqlite3.connect("cargoof.db")  # O'zingizning bazangiz nomi
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE customers ADD COLUMN full_name TEXT;")
        print("full_name ustuni qo‘shildi!")
    except sqlite3.OperationalError as e:
        print("Xatolik:", e)
    conn.commit()
    conn.close()

# --------------------------
# KEYBOARDS
# --------------------------
def role_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚖 Ҳайдовчи"), KeyboardButton(text="👤 Мижоз")]],
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

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def customer_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Янгидан буюртма")],
            [KeyboardButton(text="📝 Профиль")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def driver_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Янгидан буюртма")],
            [KeyboardButton(text="📜 Бўш буюртмалар")],
            [KeyboardButton(text="📝 Профиль"), KeyboardButton(text="💳 Баланс тўлдириш (квитансия)")],
            [KeyboardButton(text="📞 Админ билан боғланиш")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def admin_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Барча буюртмалар")],
            [KeyboardButton(text="🚖 Ҳайдовчилар"), KeyboardButton(text="👥 Мижозлар")],
            [KeyboardButton(text="💵 Баланс тўлдириш")],
            [KeyboardButton(text="📢 Хабар юбориш")],
            [KeyboardButton(text="🏠 Бош меню")]
        ],
        resize_keyboard=True
    )

def phone_request_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]], resize_keyboard=True)


class CustomerReg(StatesGroup):
    waiting_for_phone = State()
    waiting_for_fullname = State()


@router.message(F.text == "👤 Мижоз")
async def customer_start(message: Message, state: FSMContext):
    await message.answer("📱 Телефон рақамингизни юборинг:", reply_markup=phone_request_kb())
    await state.set_state(CustomerReg.waiting_for_phone)


@router.message(CustomerReg.waiting_for_phone, F.contact)
async def customer_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await message.answer("👤 Исм ва фамилиянгизни киритинг:")
    await state.set_state(CustomerReg.waiting_for_fullname)


@router.message(CustomerReg.waiting_for_fullname)
async def customer_fullname(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = data["phone"]
    full_name = message.text
    user_id = message.from_user.id
    username = message.from_user.username

    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO customers (user_id, username, phone, full_name, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (user_id, username, phone, full_name))
        conn.commit()

    await message.answer("✅ Сиз мижоз сифатида рўйхатдан ўтдингиз!", reply_markup=customer_menu_kb())
    await state.clear()


def commission_kb(order_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 000", callback_data=f"setfee:{order_id}:5000"),
         InlineKeyboardButton(text="10 000", callback_data=f"setfee:{order_id}:10000"),
         InlineKeyboardButton(text="15 000", callback_data=f"setfee:{order_id}:15000")]
    ])

def topup_amount_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 000", callback_data="adm_topup_amt:5000"),
         InlineKeyboardButton(text="10 000", callback_data="adm_topup_amt:10000")],
        [InlineKeyboardButton(text="50 000", callback_data="adm_topup_amt:50000"),
         InlineKeyboardButton(text="100 000", callback_data="adm_topup_amt:100000")],
        [InlineKeyboardButton(text="✍️ Бошқа сумма", callback_data="adm_topup_amt:other")]
    ])
# --- Admin bilan bog'lanish ---
@router.message(F.text == "📞 Админ билан боғланиш")
async def contact_admin(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍💻 Developer", url="https://t.me/zaaaza13")],
        [InlineKeyboardButton(text="👨‍💻 Admin  💻", url="https://t.me/Rabo_logos")]
    ])
    await message.answer("📞 Админ билан боғланиш учун қуйидагилардан бирини танланг:", reply_markup=kb)

# --------------------------
# FSM STATES
# --------------------------
class NewOrder(StatesGroup):
    from_address = State()
    to_address = State()
    cargo_type = State()
    car_type = State()
    cargo_weight = State()
    ask_phone = State()

class DriverRegistration(StatesGroup):
    ask_phone = State()
    ask_fullname = State()
    ask_car_model = State()
    custom_car = State()

class AdminTopUp(StatesGroup):
    choose_driver = State()
    custom_amount = State()

class ReceiptApproval(StatesGroup):
    custom_amount = State()

class Broadcast(StatesGroup):
    choose_group = State()
    message_text = State()

# --------------------------
# HELPERS
# --------------------------
def list_active_driver_ids() -> list:
    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id FROM drivers WHERE status='active'").fetchall()
        return [r["driver_id"] for r in rows]

async def push_new_order_to_drivers(order_row: sqlite3.Row):
    fee = order_row["commission"] or 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{order_row['id']}"),
                                               InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{order_row['id']}")]])
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
    with closing(db()) as conn:
        conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + ? WHERE driver_id=?", (amount, driver_id))
        conn.commit()
        new_bal = conn.execute("SELECT balance FROM drivers WHERE driver_id=?", (driver_id,)).fetchone()
        new_bal_value = int(new_bal["balance"]) if new_bal and new_bal["balance"] is not None else amount
    try:
        await bot.send_message(driver_id, f"💳 <b>Balansingiz to‘ldirildi!</b>\n\nSizga +<b>{amount}</b> сўм қўшилди ✅\n📊 Жорий баланс: <b>{new_bal_value}</b> сўм")
    except Exception:
        pass

# --------------------------
# START HANDLER
# --------------------------
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

    if customer:
        await message.answer("👋 Салом!", reply_markup=customer_menu_kb())
        return

    await message.answer("👋 Салом! Илтимос, ролингизни танланг:", reply_markup=role_kb())

# --------------------------
# ROLE CHOICES
# --------------------------
@router.message(F.text == "👤 Мижоз")
async def role_customer(message: Message):
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO customers(user_id, username, phone, status) VALUES(?,?,?,?)",
                     (message.from_user.id, f"@{message.from_user.username}" if message.from_user.username else None, None, "active"))
        conn.commit()
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

# --------------------------
# DRIVER REGISTRATION FLOW
# --------------------------
@router.message(DriverRegistration.ask_phone, F.contact)
async def driver_save_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    uname = message.from_user.username
    await state.update_data(phone=phone, username=f"@{uname}" if uname else None)
    await state.set_state(DriverRegistration.ask_fullname)
    await message.answer("👤 Илтимос, исм ва фамилиянгизни киритинг:")

@router.message(DriverRegistration.ask_fullname, F.text)
async def driver_save_fullname(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    kb = InlineKeyboardBuilder()
    kb.button(text="Labo", callback_data="car_Labo")
    kb.button(text="Bongo", callback_data="car_Bongo")
    kb.button(text="Isuzi", callback_data="car_Isuzi")
    kb.button(text="Gazel", callback_data="car_Gazel")
    kb.button(text="Boshqa", callback_data="car_other")
    kb.adjust(2)
    await state.set_state(DriverRegistration.ask_car_model)
    await message.answer("🚘 Машина русумини танланг:", reply_markup=kb.as_markup())

@router.callback_query(DriverRegistration.ask_car_model, F.data.startswith("car_"))
async def driver_choose_car(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split("_", 1)[1]
    if choice == "other":
        await callback.message.answer("✍️ Иложи борича ўз машина русумини ёзинг:")
        await state.set_state(DriverRegistration.custom_car)
    else:
        data = await state.get_data()
        uname = data.get("username")
        phone = data.get("phone")
        full_name = data.get("full_name")
        with closing(db()) as conn:
            conn.execute("""
                INSERT INTO drivers(driver_id, username, phone, full_name, car_model, balance, status)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(driver_id) DO UPDATE SET
                    username=excluded.username,
                    phone=excluded.phone,
                    full_name=excluded.full_name,
                    car_model=excluded.car_model
            """, (callback.from_user.id, uname, phone, full_name, choice, 99000, "active"))
            conn.commit()
        for aid in ADMIN_IDS:
            try:
                await bot.send_message(aid, f"🚨 Янги ҳайдовчи рўйхатдан ўтди: @{uname or callback.from_user.id} | ID: {callback.from_user.id}\n👤 {full_name}\n🚘 {choice}")
            except Exception:
                pass
        await state.clear()
        await callback.message.answer(f"✅ Рўйхатдан ўтдингиз!\n👤 {full_name}\n🚘 {choice}\n💰 Bonus: 99 000 сўм", reply_markup=driver_menu_kb())
    await callback.answer()

@router.message(DriverRegistration.custom_car, F.text)
async def driver_custom_car(message: Message, state: FSMContext):
    data = await state.get_data()
    car_model = message.text.strip()
    uname = data.get("username")
    phone = data.get("phone")
    full_name = data.get("full_name")
    with closing(db()) as conn:
        conn.execute("""
            INSERT INTO drivers(driver_id, username, phone, full_name, car_model, balance, status)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(driver_id) DO UPDATE SET
                username=excluded.username,
                phone=excluded.phone,
                full_name=excluded.full_name,
                car_model=excluded.car_model
        """, (message.from_user.id, uname, phone, full_name, car_model, 99000, "active"))
        conn.commit()
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, f"🚨 Янги ҳайдовчи рўйхатдан ўтди: @{uname or message.from_user.id} | ID: {message.from_user.id}\n👤 {full_name}\n🚘 {car_model}")
        except Exception:
            pass
    await state.clear()
    await message.answer(f"✅ Рўйхатдан ўтдингиз!\n👤 {full_name}\n🚘 {car_model}\n💰 Bonus: 99 000 сўм", reply_markup=driver_menu_kb())

# --------------------------
# NEW ORDER FLOW (customer or driver)
# --------------------------
@router.message(F.text == "📝 Янгидан буюртма")
async def new_order(message: Message, state: FSMContext):
    with closing(db()) as conn:
        drv = conn.execute("SELECT status FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
        cust = conn.execute("SELECT status FROM customers WHERE user_id=?", (message.from_user.id,)).fetchone()
    if drv and drv["status"] == "blocked":
        await message.answer("❗ Сиз блокланган ҳайдовчисиз. Админга мурожаат қилинг.")
        return
    if cust and cust["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз. Админга мурожаат қилинг.")
        return
    creator_role = "driver" if drv else "customer"
    await state.update_data(creator_role=creator_role)
    await state.set_state(NewOrder.from_address)
    await message.answer("📍 Қаердан юк олинади? Манзилни киритинг:", reply_markup=ReplyKeyboardRemove())

@router.message(NewOrder.from_address, F.text)
async def order_from(message: Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("❌ Манзилни тўлиқроқ киритинг.")
        return
    await state.update_data(from_address=text)
    await state.set_state(NewOrder.to_address)
    await message.answer("📍 Қаерга юборилади? Манзилни киритинг:")

@router.message(NewOrder.to_address, F.text)
async def order_to(message: Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("❌ Манзилни тўлиқроқ киритинг.")
        return
    await state.update_data(to_address=text)
    await state.set_state(NewOrder.cargo_type)
    await message.answer("📦 Юк турини киритинг:")

@router.message(NewOrder.cargo_type, F.text)
async def order_cargo(message: Message, state: FSMContext):
    text = message.text.strip()
    await state.update_data(cargo_type=text)
    await state.set_state(NewOrder.car_type)
    await message.answer("🚘 Қайси машина керак? Тугмалардан танланг:", reply_markup=car_type_kb())

@router.message(NewOrder.car_type, F.text)
async def order_car(message: Message, state: FSMContext):
    if message.text == "⬅️ Бекор қилиш":
        await state.clear()
        with closing(db()) as conn:
            drv = conn.execute("SELECT * FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
        await message.answer("❌ Буюртма бекор қилинди.", reply_markup=driver_menu_kb() if drv else customer_menu_kb())
        return
    if message.text not in ["🚐 Лабо", "🚛 Бонго", "🚚 Исузи"]:
        await message.answer("❌ Илтимос, тугмалардан танланг.")
        return
    await state.update_data(car_type=message.text)
    await state.set_state(NewOrder.cargo_weight)
    await message.answer("⚖️ Юк оғирлигини киритинг (кг):", reply_markup=ReplyKeyboardRemove())

@router.message(NewOrder.cargo_weight, F.text)
async def order_weight(message: Message, state: FSMContext):
    txt = message.text.strip().replace(",", ".")
    if not re.match(r"^\d+(\.\d+)?$", txt):
        await message.answer("❌ Фақат рақам киритинг (масалан: 150 ёки 75.5).")
        return
    await state.update_data(cargo_weight=float(txt))
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

    with closing(db()) as conn:
        conn.execute("""
            INSERT INTO orders(customer_id, from_address, to_address, cargo_type, car_type, cargo_weight, date, status, customer_username, customer_phone, creator_role)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (message.from_user.id, data["from_address"], data["to_address"], data["cargo_type"], data["car_type"], data["cargo_weight"], now, "pending_fee", customer_username, phone, creator_role))
        conn.commit()
        order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        conn.execute("INSERT OR REPLACE INTO customers(user_id, username, phone, status) VALUES(?,?,?, COALESCE((SELECT status FROM customers WHERE user_id=?), 'active'))",
                     (message.from_user.id, customer_username, phone, message.from_user.id))
        conn.commit()

    await state.clear()
    await message.answer(f"✅ Буюртмангиз #{order_id} қабул қилинди!\nАдмин томонидан комиссия белгиланади.", reply_markup=driver_menu_kb() if creator_role=="driver" else customer_menu_kb())

    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    text_admin = (
        f"🆕 <b>Янги буюртма!</b>\n\n"
        f"🆔 {order['id']} | {order['date']}\n"
        f"📍 {order['from_address']} ➜ {order['to_address']}\n"
        f"📦 {order['cargo_type']}\n"
        f"🚘 {order['car_type']}\n"
        f"⚖️ {order['cargo_weight']} кг\n"
        f"👤 {order['customer_username']}\n"
        f"📞 {order['customer_phone']}\n\n"
        f"Комиссияни танланг:"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text_admin, reply_markup=commission_kb(order_id))
        except Exception:
            pass

# --------------------------
# ADMIN set commission
# --------------------------
@router.callback_query(F.data.startswith("setfee:"))
async def set_fee(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True)
        return
    try:
        _, oid, fee = callback.data.split(":")
        order_id = int(oid); fee = int(fee)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True)
        return
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            await callback.answer("Буюртма топилмади.", show_alert=True)
            return
        if row["status"] != "pending_fee":
            await callback.answer("Комиссия аллақачон белгиланган.", show_alert=True)
            return
        conn.execute("UPDATE orders SET commission=?, status='open' WHERE id=?", (fee, order_id))
        conn.commit()
    await callback.answer("Комиссия ўрнатилди ва ҳайдовчиларга юборилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    await push_new_order_to_drivers(order)

# --------------------------
# DRIVER: free orders & accept/reject
# --------------------------
@router.message(F.text == "📜 Бўш буюртмалар")
async def free_orders(message: Message):
    with closing(db()) as conn:
        d = conn.execute("SELECT status FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if not d:
        await message.answer("❌ Ҳайдовчи сифатида рўйхатдан ўтинг.", reply_markup=role_kb())
        return
    if d["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз. Админга мурожаат қилинг.")
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await message.answer("📭 Ҳозирча бўш буюртма йўқ.")
        return
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{r['id']}"),
            InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{r['id']}")
        ]])
        await message.answer(format_order_row(r), reply_markup=kb)

@router.callback_query(F.data.startswith("accept:"))
async def accept_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        d = conn.execute("SELECT balance, phone, username, status FROM drivers WHERE driver_id=?", (callback.from_user.id,)).fetchone()
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not d:
        await callback.answer("❌ Ҳайдовчи сифатида рўйхатдан ўтинг.", show_alert=True); return
    if d["status"] == "blocked":
        await callback.answer("❗ Сиз блоклангансиз.", show_alert=True); return
    if not order or order["status"] != "open":
        await callback.answer("❌ Буюртма қолмаган ёки олган.", show_alert=True); return

    fee = int(order["commission"] or 0)
    if (d["balance"] or 0) < fee:
        await callback.answer(f"❌ Балансингиз етарли эмас. Керак: {fee} сўм.", show_alert=True); return

    # buyurtmani haydovchiga biriktirish
    with closing(db()) as conn:
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

    # buyurtmachiga habar
    try:
        await bot.send_message(
            order["customer_id"],
            f"✅ Сизнинг буюртмангиз #{order_id} ҳайдовчи томонидан қабул қилинди!\n👤 {d['username'] or callback.from_user.id}\n📞 {d['phone'] or '—'}"
        )
    except Exception:
        pass

    # haydovchiga buyurtma tafsilotlari va yakunlash tugmasi
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Buyurtmani yakunlash", callback_data=f"complete:{order_id}")]
    ])
    text_driver = (
        f"🆕 Сизга буюртма бириктирилди!\n\n"
        f"📍 {order['from_address']} ➜ {order['to_address']}\n"
        f"📦 {order['cargo_type']}\n"
        f"⚖️ {order['cargo_weight']} кг\n"
        f"👤 Buyurtmachi: {order['customer_username'] or '—'}\n"
        f"📞 Телефон: {order['customer_phone'] or '—'}"
    )
    await bot.send_message(callback.from_user.id, text_driver, reply_markup=kb)


@router.callback_query(F.data.startswith("reject:"))
async def reject_order(callback: CallbackQuery):
    await callback.answer("❌ Сиз рад этдингиз.")
# --------------------------
# COMPLETE ORDER (driver presses complete inline)
# --------------------------
@router.callback_query(F.data.startswith("complete:"))
async def complete_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            await callback.answer("❌ Буюртма топилмади.", show_alert=True); return
        if order["driver_id"] != callback.from_user.id:
            await callback.answer("❌ Фақат ушбу ҳайдовчи якунлайди.", show_alert=True); return
        if order["status"] != "taken":
            await callback.answer("❌ Ҳолат мос эмас.", show_alert=True); return
        conn.execute("UPDATE orders SET status='done' WHERE id=?", (order_id,))
        conn.commit()
    await callback.answer("✅ Буюртма якунланди!", show_alert=True)
    try:
        await bot.send_message(order["customer_id"], f"🚚 Сизнинг буюртмангиз #{order_id} якунланди.")
    except Exception:
        pass
# --------------------------
# BALANCE & RECEIPTS
# --------------------------
# --- Профиль ---
@router.message(F.text == "💳 Баланс тўлдириш (квитансия)")
async def send_receipt_instructions(message: Message):
    with closing(db()) as conn:
        drv = conn.execute(
            "SELECT driver_id FROM drivers WHERE driver_id=?",
            (message.from_user.id,)
        ).fetchone()

    if not drv and message.from_user.id not in ADMIN_IDS:
        await message.answer("❗ Фақат ҳайдовчилар ва админлар учун.")
        return

    if drv:
        text = (
            "💳 <b>Баланс тўлдириш бўйича кўрсатмалар</b>\n\n"
            "🏦 Банк: <b>Kapitalbank</b>\n"
            "💳 Карта рақами: <code>8600 1234 5678 9012</code>\n"
            "👤 Эгаси: <b>Исломов Ислом Исломович</b>\n\n"
            f"📌 Тўлов қилаётганда изоҳга ўз <b>Telegram ID</b> рақамингизни ёзинг:\n<code>{message.from_user.id}</code>\n\n"
            "✅ Тўловни амалга оширгандан кейин, квитанция (скриншот)ни шу ерга юборинг."
        )
        await message.answer(text, parse_mode="HTML")
    else:
        await message.answer("🔧 Админ учун: Баланс тўлдириш менюси.")

@router.message(F.photo)
async def handle_receipt_and_forward(message: Message):
    with closing(db()) as conn:
        drv = conn.execute("SELECT driver_id, username FROM drivers WHERE driver_id=?", (message.from_user.id,)).fetchone()
    if not drv:
        return
    file_id = message.photo[-1].file_id
    with closing(db()) as conn:
        conn.execute("INSERT INTO receipts(driver_id, file_id, status) VALUES(?,?, 'pending')", (message.from_user.id, file_id))
        conn.commit()
        receipt_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    caption = (f"🧾 Квитанция #{receipt_id}\n"
               f"🧑‍✈️ Haydovchi: @{drv['username'] or message.from_user.id}\n"
               f"📞 ID: {message.from_user.id}\n\n"
               "Қабул қилинган квитанцияни тасдиқланг ёки рад этинг.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+5 000", callback_data=f"approve_receipt:{receipt_id}:5000"),
         InlineKeyboardButton(text="+10 000", callback_data=f"approve_receipt:{receipt_id}:10000"),
         InlineKeyboardButton(text="+15 000", callback_data=f"approve_receipt:{receipt_id}:15000")],
        [InlineKeyboardButton(text="✍️ Бошқа сумма", callback_data=f"approve_receipt_other:{receipt_id}"),
         InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject_receipt:{receipt_id}")]
    ])
    for aid in ADMIN_IDS:
        try:
            await bot.send_photo(aid, file_id, caption=caption, reply_markup=kb)
        except Exception:
            pass
    await message.answer("📩 Квитанция админга юборилди. Тез орада текширилади.")

def get_driver_info(user_id):
    from contextlib import closing
    # Bu yerda sening db() funksiyangizni ishlatasiz
    with closing(db()) as conn:
        drv = conn.execute(
            "SELECT full_name, car_model, phone, username, balance FROM drivers WHERE driver_id=?",
            (user_id,)
        ).fetchone()
        return drv

@router.callback_query(F.data.startswith("approve_receipt:"))
async def approve_receipt_fixed(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True); return
    try:
        _, receipt_id_s, amount_s = callback.data.split(":")
        receipt_id = int(receipt_id_s); amount = int(amount_s)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True); return
    with closing(db()) as conn:
        rec = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if not rec:
            await callback.answer("Квитанция топилмади.", show_alert=True); return
        if rec["status"] != "pending":
            await callback.answer("Квитанция аллақачон кўриб чиқилган.", show_alert=True); return
        conn.execute("UPDATE receipts SET status='approved' WHERE id=?", (receipt_id,))
        conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + ? WHERE driver_id=?", (amount, rec["driver_id"]))
        conn.commit()
    try:
        await bot.send_message(rec["driver_id"], f"✅ Сиз юборган квитанция тасдиқланди. Балансингизга +{amount} сўм қўшилди.")
    except Exception:
        pass
    await callback.answer(f"✅ {amount} сўм — қўшилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("approve_receipt_other:"))
async def approve_receipt_other(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True); return
    try:
        _, receipt_id_s = callback.data.split(":")
        receipt_id = int(receipt_id_s)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True); return
    await state.update_data(receipt_id=receipt_id)
    await state.set_state(ReceiptApproval.custom_amount)
    await callback.message.answer("✍️ Иltimos, қўшиладиган суммани сўмда (бутун рақам) киритинг:")
    await callback.answer()

@router.message(ReceiptApproval.custom_amount, F.text)
async def receipt_custom_amount_input(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    txt = message.text.strip().replace(" ", "")
    if not txt.isdigit():
        await message.answer("❗ Иложи борича фақат бутун сони киритинг (масалан: 75000).")
        return
    amount = int(txt)
    data = await state.get_data()
    receipt_id = data.get("receipt_id")
    if receipt_id is None:
        await message.answer("Ички хатолик — квитансия ID топилмади.")
        await state.clear()
        return
    with closing(db()) as conn:
        rec = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if not rec:
            await message.answer("Квитанция топилмади.")
            await state.clear()
            return
        if rec["status"] != "pending":
            await message.answer("Квитанция аллақачон кўриб чиқилган.")
            await state.clear()
            return
        conn.execute("UPDATE receipts SET status='approved' WHERE id=?", (receipt_id,))
        conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + ? WHERE driver_id=?", (amount, rec["driver_id"]))
        conn.commit()
    try:
        await bot.send_message(rec["driver_id"], f"✅ Сиз юборган квитанция тасдиқланди. Балансингизга +{amount} сўм қўшилди.")
    except Exception:
        pass
    await message.answer(f"✅ Квитанция тасдиқланди ва {amount} сўм қўшилди.")
    await state.clear()

@router.callback_query(F.data.startswith("reject_receipt:"))
async def reject_receipt_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True); return
    try:
        _, receipt_id_s = callback.data.split(":")
        receipt_id = int(receipt_id_s)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True); return
    with closing(db()) as conn:
        rec = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if not rec:
            await callback.answer("Квитанция топилмади.", show_alert=True); return
        if rec["status"] != "pending":
            await callback.answer("Квитанция аллақачон кўриб чиқилган.", show_alert=True); return
        conn.execute("UPDATE receipts SET status='rejected' WHERE id=?", (receipt_id,))
        conn.commit()
    try:
        await bot.send_message(rec["driver_id"], "❌ Сиз юборган квитанция рад этилди. Илтимос, қайта юборинг.")
    except Exception:
        pass
    await callback.answer("❌ Квитанция рад этилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# --------------------------
# ADMIN: lists & block/unblock
# --------------------------
@router.message(F.text == "📊 Барча буюртмалар")
async def all_orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 50").fetchall()
    if not rows:
        await message.answer("📭 Буюртмалар йўқ.")
        return
    for r in rows:
        await message.answer(format_order_row(r))

@router.message(F.text == "🚖 Ҳайдовчилар")
async def list_drivers_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM drivers ORDER BY driver_id DESC").fetchall()
    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ.")
        return
    for r in rows:
        status = r["status"] or "active"
        text = f"🆔 {r['driver_id']} | {r['username'] or '—'} | 📞 {r['phone'] or '—'} | 💰 {int(r['balance'] or 0)} сўм | Статус: <b>{status}</b>\n👤 {r['full_name'] or '—'} | 🚘 {r['car_model'] or '—'}"
        if status == "active":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 Блоклаш", callback_data=f"drv_block:{r['driver_id']}")]])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Блокдан чиқариш", callback_data=f"drv_unblock:{r['driver_id']}")]])
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("drv_block:"))
async def drv_block(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    driver_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        conn.execute("UPDATE drivers SET status='blocked' WHERE driver_id=?", (driver_id,))
        conn.commit()
    try:
        await bot.send_message(driver_id, "🚫 Сиз админ томонидан блокландингиз. Илтимос админ билан боғланинг.")
    except Exception:
        pass
    await callback.answer(f"🔒 {driver_id} блокланди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("drv_unblock:"))
async def drv_unblock(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    driver_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        conn.execute("UPDATE drivers SET status='active' WHERE driver_id=?", (driver_id,))
        conn.commit()
    try:
        await bot.send_message(driver_id, "✅ Сиз блокдан чиқарилдингиз. Ботдан фойдаланинг.")
    except Exception:
        pass
    await callback.answer(f"✅ {driver_id} блокдан чиқарилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.message(F.text == "👥 Мижозлар")
async def list_customers_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY user_id DESC").fetchall()
    if not rows:
        await message.answer("📭 Мижозлар йўқ.")
        return
    for r in rows:
        status = r["status"] or "active"
        text = f"🆔 {r['user_id']} | {r['username'] or '—'} | 📞 {r['phone'] or '—'} | Статус: <b>{status}</b>"
        if status == "active":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 Блоклаш", callback_data=f"cust_block:{r['user_id']}")]])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Блокдан чиқариш", callback_data=f"cust_unblock:{r['user_id']}")]])
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("cust_block:"))
async def cust_block(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    user_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        conn.execute("UPDATE customers SET status='blocked' WHERE user_id=?", (user_id,))
        conn.commit()
    try:
        await bot.send_message(user_id, "🚫 Сиз админ томонидан блокландингиз. Илтимос админ билан боғланинг.")
    except Exception:
        pass
    await callback.answer("🔒 Мижоз блокланди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("cust_unblock:"))
async def cust_unblock(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return
    user_id = int(callback.data.split(":")[1])
    with closing(db()) as conn:
        conn.execute("UPDATE customers SET status='active' WHERE user_id=?", (user_id,))
        conn.commit()
    try:
        await bot.send_message(user_id, "✅ Сиз блокдан чиқарилдингиз. Ботдан фойдаланинг.")
    except Exception:
        pass
    await callback.answer("✅ Мижоз блокдан чиқарилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# --------------------------
# ADMIN: BROADCAST
# --------------------------
@router.message(F.text == "📢 Хабар юбориш")
async def broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚖 Ҳайдовчиларга", callback_data="broadcast_drivers")],
        [InlineKeyboardButton(text="👥 Мижозларга", callback_data="broadcast_customers")],
        [InlineKeyboardButton(text="🌍 Ҳаммасига", callback_data="broadcast_all")]
    ])
    await state.set_state(Broadcast.choose_group)
    await message.answer("📢 Кимларга хабар юборишни танланг:", reply_markup=kb)

@router.callback_query(Broadcast.choose_group, F.data.startswith("broadcast_"))
async def choose_broadcast_group(callback: CallbackQuery, state: FSMContext):
    group = callback.data.replace("broadcast_", "")
    await state.update_data(group=group)
    await state.set_state(Broadcast.message_text)
    await callback.message.answer("✍️ Юбориладиган хабар матнини киритинг:")
    await callback.answer()

@router.message(Broadcast.message_text, F.text)
async def send_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    group = data.get("group")
    text = message.text
    await state.clear()

    with closing(db()) as conn:
        drivers = conn.execute("SELECT driver_id FROM drivers").fetchall()
        customers = conn.execute("SELECT user_id FROM customers").fetchall()

    targets = []
    if group == "drivers":
        targets = [r["driver_id"] for r in drivers]
    elif group == "customers":
        targets = [r["user_id"] for r in customers]
    else:
        targets = [r["driver_id"] for r in drivers] + [r["user_id"] for r in customers]

    sent = 0
    failed = 0
    for uid in targets:
        try:
            await bot.send_message(uid, f"📢 <b>Админ хабар:</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.03)
        except Exception:
            failed += 1
    await message.answer(f"✅ Хабар юборилди.\n📨 Жами: {sent} та\n❌ Юборилмади: {failed} та", reply_markup=admin_menu_kb())

# --------------------------
# ADMIN: BALANCE TOPUP (choose driver -> amount)
# --------------------------
@router.message(F.text == "💵 Баланс тўлдириш")
async def admin_topup_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Фақат админлар учун")
        return

    with closing(db()) as conn:
        rows = conn.execute("SELECT driver_id, username FROM drivers ORDER BY driver_id DESC").fetchall()

    if not rows:
        await message.answer("📭 Ҳайдовчилар йўқ.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{r['username'] or r['driver_id']}", callback_data=f"adm_topup_driver:{r['driver_id']}")] for r in rows
    ])

    await state.set_state(AdminTopUp.choose_driver)
    await message.answer("👤 Қайси ҳайдовчига баланс қўшасиз? Танланг:", reply_markup=kb)

@router.callback_query(F.data.startswith("adm_topup_driver:"))
async def adm_topup_driver_chosen(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return

    driver_id = int(callback.data.split(":", 1)[1])
    await state.update_data(driver_id=driver_id)

    await callback.message.answer("💵 Миқдорни танланг ёки «✍️ Бошқа сумма»ни танлаб суммани киритинг:", reply_markup=topup_amount_kb())
    await state.set_state(AdminTopUp.custom_amount)
    await callback.answer()

@router.callback_query(F.data.startswith("adm_topup_amt:"))
async def adm_topup_amount_choice(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(); return

    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    driver_id = data.get("driver_id")
    if driver_id is None:
        await callback.answer("Ички хатолик."); return

    if choice == "other":
        await callback.message.answer("✍️ Суммани бутун рақамда киритинг (сўм):")
        await state.set_state(AdminTopUp.custom_amount)
        await callback.answer()
        return

    amount = int(choice)
    with closing(db()) as conn:
        conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + ? WHERE driver_id=?", (amount, driver_id))
        conn.commit()

    await callback.answer(f"✅ Баланс {amount} сўм қўшилди.", show_alert=True)
    try:
        await bot.send_message(driver_id, f"💳 Балансингизга +{amount} сўм қўшилди (админ).")
    except Exception:
        pass
    await state.clear()

@router.message(AdminTopUp.custom_amount, F.text)
async def adm_topup_custom_amount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    txt = message.text.strip().replace(" ", "")
    if not txt.isdigit():
        await message.answer("❗ Фақат бутун рақам киритинг (масалан: 75000).")
        return
    amount = int(txt)
    data = await state.get_data()
    driver_id = data.get("driver_id")
    if driver_id is None:
        await message.answer("Ички хатолик — driver_id топилмади.")
        await state.clear()
        return

    with closing(db()) as conn:
        conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + ? WHERE driver_id=?", (amount, driver_id))
        conn.commit()

    await message.answer(f"✅ Баланс {driver_id} учун +{amount} сўм қўшилди.", reply_markup=admin_menu_kb())
    try:
        await bot.send_message(driver_id, f"💳 Админ томонидан балансингизга +{amount} сўм қўшилди.")
    except Exception:
        pass
    await state.clear()

# --------------------------
# CONTACT ADMIN (inline buttons -> open telegram / call)
# --------------------------
@router.message(F.text == "📞 Админ билан боғланиш")
async def contact_admin(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Admin 1", url="https://t.me/zaaaza13")],
        [InlineKeyboardButton(text="👤 Mirzayev Pro", url="https://t.me/dezard7177")],
        [InlineKeyboardButton(text="📞 +998330131992", url="tel:+998330131992")],
        [InlineKeyboardButton(text="📞 +998885131111", url="tel:+998885131111")]
    ])
    await message.answer("📞 Админ билан боғланиш учун қуйидагилардан бирини танланг:", reply_markup=kb)

# --------------------------
# HOME
# --------------------------
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

@router.message(F.text == "📝 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    with db() as conn:
        cur = conn.cursor()

        # Haydovchi profil
        cur.execute("SELECT * FROM drivers WHERE driver_id=?", (user_id,))
        driver = cur.fetchone()
        if driver:
            text = (
                f"👤 <b>Ҳайдовчи профили</b>\n\n"
                f"🆔 ID: <code>{driver['driver_id']}</code>\n"
                f"👤 Исм: {driver['full_name'] or '—'}\n"
                f"📞 Телефон: {driver['phone'] or '—'}\n"
                f"🚗 Машина: {driver['car_model'] or '—'}\n"
                f"💳 Баланс: {driver['balance'] or 0} сўм\n"
                f"📌 Статус: {driver['status'] or '—'}\n"
            )
            await message.answer(text, reply_markup=driver_menu_kb())
            return

        # Mijoz profil
        cur.execute("SELECT * FROM customers WHERE user_id=?", (user_id,))
        customer = cur.fetchone()
        if customer:
            text = (
                f"👤 <b>Мижоз профили</b>\n\n"
                f"🆔 ID: <code>{customer['user_id']}</code>\n"
                f"👤 Исм: {customer['full_name'] or '—'}\n"
                f"👤 Username: @{message.from_user.username or '-'}\n"
                f"📞 Телефон: {customer['phone'] or '—'}\n"
                f"📌 Статус: {customer['status'] or '—'}\n"
            )
            await message.answer(text, reply_markup=customer_menu_kb())
            return

    await message.answer("❌ Сиз рўйхатдан ўтмагансиз!", reply_markup=role_kb())# --------------------------
# START POLLING
# --------------------------
async def main():
    init_db()
    print("🚀 Bot ишга тушди...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


# --- Профиль ---
