# -*- coding: utf-8 -*-
"""
cargobot_updated.py
To'liq ishlaydigan bot (asyncpg + PostgreSQL, Railway uchun)
Asl kod postgresga moslashtirildi:
- sqlite3 olib tashlandi
- asyncpg pool ishlatiladi
- barcha `with closing(db())` -> `async with pool.acquire()` ga almashtirildi
- INSERT ... RETURNING id qo'llanildi
"""
import asyncio
import asyncpg
import re
import os
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
# add your admin IDs here (integers)
ADMIN_IDS = {1262207928, 8011859232}
DATABASE_URL = os.getenv("DATABASE_URL")  # must be set in env

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
pool: asyncpg.Pool = None

async def init_db():
    """
    Init connection pool and create tables if not exists.
    """
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in environment variables.")
    # ensure prefix is postgresql:// (asyncpg prefers this)
    dburl = DATABASE_URL
    if dburl.startswith("postgres://"):
        dburl = dburl.replace("postgres://", "postgresql://", 1)
    # create pool
    # if you have issues with SSL, you may add ssl=False parameter
    pool = await asyncpg.create_pool(dsn=dburl)

    async with pool.acquire() as conn:
        # create tables (adapted from original sqlite schemas)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS drivers(
            driver_id BIGINT PRIMARY KEY,
            username TEXT,
            phone TEXT,
            full_name TEXT,
            car_model TEXT,
            balance NUMERIC DEFAULT 0,
            status TEXT DEFAULT 'active'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            phone TEXT,
            full_name TEXT,
            status TEXT DEFAULT 'active'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id SERIAL PRIMARY KEY,
            customer_id BIGINT,
            from_address TEXT,
            to_address TEXT,
            cargo_type TEXT,
            car_type TEXT,
            cargo_weight NUMERIC,
            date TEXT,
            status TEXT DEFAULT 'pending_fee',
            driver_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            customer_username TEXT,
            customer_phone TEXT,
            commission INTEGER,
            creator_role TEXT DEFAULT 'customer'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS receipts(
            id SERIAL PRIMARY KEY,
            driver_id BIGINT,
            file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
    # pool ready
    return pool

# --------------------------
# BALANSE KORINISHI OZGARADI
def format_sum(amount):
    if amount is None:
        return "0"
    return f"{int(amount):,}".replace(",", " ")

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


# --------------------------
# FSM STATES
# --------------------------
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

    async with pool.acquire() as conn:
        # insert or update customer
        await conn.execute("""
            INSERT INTO customers (user_id, username, phone, full_name, status)
            VALUES($1, $2, $3, $4, 'active')
            ON CONFLICT (user_id) DO UPDATE
              SET username = EXCLUDED.username,
                  phone = EXCLUDED.phone,
                  full_name = EXCLUDED.full_name,
                  status = COALESCE(customers.status, 'active')
        """, user_id, f"@{username}" if username else None, phone, full_name)

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
# FSM STATES (continued)
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
async def list_active_driver_ids() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT driver_id FROM drivers WHERE status='active'")
        return [r["driver_id"] for r in rows]

async def push_new_order_to_drivers(order_row):
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
    for did in await list_active_driver_ids():
        try:
            await bot.send_message(did, text, reply_markup=kb)
        except Exception:
            pass

def format_order_row(r) -> str:
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + $1 WHERE driver_id=$2", amount, driver_id)
        row = await conn.fetchrow("SELECT balance FROM drivers WHERE driver_id=$1", driver_id)
        new_bal_value = int(row["balance"]) if row and row["balance"] is not None else amount
    try:
        await bot.send_message(driver_id, f"💳 <b>Balansingiz to‘ldirildi!</b>\n\nSizga +<b>{format_sum(amount)}</b> сўм қўшилди ✅\n📊 Жорий баланс: <b>{new_bal_value}</b> сўм")
    except Exception:
        pass

# --------------------------
# START HANDLER
# --------------------------
@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    async with pool.acquire() as conn:
        driver = await conn.fetchrow("SELECT * FROM drivers WHERE driver_id=$1", message.from_user.id)
        customer = await conn.fetchrow("SELECT * FROM customers WHERE user_id=$1", message.from_user.id)

    if message.from_user.id in ADMIN_IDS:
        await message.answer("<b>👑 Админ интерфейс</b>\n\nАдмин менюдан бирор бўлимни танланг:", reply_markup=admin_menu_kb())
        return

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
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO customers(user_id, username, phone, status) VALUES($1,$2,$3,$4) ON CONFLICT (user_id) DO NOTHING",
                           message.from_user.id, f"@{message.from_user.username}" if message.from_user.username else None, None, "active")
    await message.answer("✅ Сиз мижоз сифатида рўйхатдан ўтдингиз!", reply_markup=customer_menu_kb())

@router.message(F.text == "🚖 Ҳайдовчи")
async def role_driver(message: Message, state: FSMContext):
    async with pool.acquire() as conn:
        drv = await conn.fetchrow("SELECT status FROM drivers WHERE driver_id=$1", message.from_user.id)
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
        async with pool.acquire() as conn:
            # upsert driver
            await conn.execute("""
                INSERT INTO drivers(driver_id, username, phone, full_name, car_model, balance, status)
                VALUES($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (driver_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    phone = EXCLUDED.phone,
                    full_name = EXCLUDED.full_name,
                    car_model = EXCLUDED.car_model
            """, callback.from_user.id, uname, phone, full_name, choice, 99000, "active")
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
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO drivers(driver_id, username, phone, full_name, car_model, balance, status)
            VALUES($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (driver_id) DO UPDATE SET
                username = EXCLUDED.username,
                phone = EXCLUDED.phone,
                full_name = EXCLUDED.full_name,
                car_model = EXCLUDED.car_model
        """, message.from_user.id, uname, phone, full_name, car_model, 99000, "active")
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
    async with pool.acquire() as conn:
        drv = await conn.fetchrow("SELECT status FROM drivers WHERE driver_id=$1", message.from_user.id)
        cust = await conn.fetchrow("SELECT status FROM customers WHERE user_id=$1", message.from_user.id)
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
        async with pool.acquire() as conn:
            drv = await conn.fetchrow("SELECT * FROM drivers WHERE driver_id=$1", message.from_user.id)
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

    async with pool.acquire() as conn:
        # Use RETURNING id to obtain order id
        rec = await conn.fetchrow("""
            INSERT INTO orders(customer_id, from_address, to_address, cargo_type, car_type, cargo_weight, date, status, customer_username, customer_phone, creator_role)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id
        """, message.from_user.id, data["from_address"], data["to_address"], data["cargo_type"], data["car_type"], data["cargo_weight"], now, "pending_fee", customer_username, phone, creator_role)
        order_id = rec["id"] if rec else None

        # upsert customer record
        await conn.execute("""
            INSERT INTO customers(user_id, username, phone, status)
            VALUES($1,$2,$3, COALESCE((SELECT status FROM customers WHERE user_id=$1), 'active'))
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                phone = EXCLUDED.phone
        """, message.from_user.id, customer_username, phone)

    await state.clear()
    await message.answer(f"✅ Буюртмангиз #{order_id} қабул қилинди!\nАдмин томонидан комиссия белгиланади.", reply_markup=driver_menu_kb() if creator_role=="driver" else customer_menu_kb())

    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
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
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
        if not row:
            await callback.answer("Буюртма топилмади.", show_alert=True)
            return
        if row["status"] != "pending_fee":
            await callback.answer("Комиссия аллақачон белгиланган.", show_alert=True)
            return
        await conn.execute("UPDATE orders SET commission=$1, status='open' WHERE id=$2", fee, order_id)
    await callback.answer("Комиссия ўрнатилди ва ҳайдовчиларга юборилди.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
    await push_new_order_to_drivers(order)

# --------------------------
# DRIVER: free orders & accept/reject
# --------------------------
@router.message(F.text == "📜 Бўш буюртмалар")
async def free_orders(message: Message):
    async with pool.acquire() as conn:
        d = await conn.fetchrow("SELECT status FROM drivers WHERE driver_id=$1", message.from_user.id)
    if not d:
        await message.answer("❌ Ҳайдовчи сифатида рўйхатдан ўтинг.", reply_markup=role_kb())
        return
    if d["status"] == "blocked":
        await message.answer("❗ Сиз блоклангансиз. Админга мурожаат қилинг.")
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 20")
    if not rows:
        await message.answer("📭 Ҳозирча бўш буюртма йўқ.")
        return
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{r['id']}"),
            InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{r['id']}")]])
        await message.answer(format_order_row(r), reply_markup=kb)

@router.callback_query(F.data.startswith("accept:"))
async def accept_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    async with pool.acquire() as conn:
        d = await conn.fetchrow("SELECT balance, phone, username, status FROM drivers WHERE driver_id=$1", callback.from_user.id)
        order = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
    if not d:
        await callback.answer("❌ Ҳайдовчи сифатида рўйхатдан ўтинг.", show_alert=True); return
    if d["status"] == "blocked":
        await callback.answer("❗ Сиз блоклангансиз.", show_alert=True); return
    if not order or order["status"] != "open":
        await callback.answer("❌ Буюртма қолмаган ёки олган.", show_alert=True); return

    fee = int(order["commission"] or 0)
    if (d["balance"] or 0) < fee:
        await callback.answer(f"❌ Балансингиз етарли эмас. Керак: {fee} сўм.", show_alert=True); return

    # Assign order to driver atomically
    async with pool.acquire() as conn:
        # ensure still open
        row = await conn.fetchrow("SELECT status FROM orders WHERE id=$1", order_id)
        if not row or row["status"] != "open":
            await callback.answer("❌ Кечикдингиз, буюртма банд бўлди.", show_alert=True); return
        await conn.execute("UPDATE orders SET status='taken', driver_id=$1 WHERE id=$2 AND status='open'", callback.from_user.id, order_id)
        await conn.execute("UPDATE drivers SET balance = balance - $1 WHERE driver_id=$2", fee, callback.from_user.id)

    await callback.answer("✅ Буюртма қабул қилинди!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # notify customer
    try:
        await bot.send_message(
            order["customer_id"],
            f"✅ Сизнинг буюртмангиз #{order_id} ҳайдовчи томонидан қабул қилинди!\n👤 {d['username'] or callback.from_user.id}\n📞 {d['phone'] or '—'}"
        )
    except Exception:
        pass

    # send details to driver with complete button
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
    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
        if not order:
            await callback.answer("❌ Буюртма топилмади.", show_alert=True); return
        if order["driver_id"] != callback.from_user.id:
            await callback.answer("❌ Фақат ушбу ҳайдовчи якунлайди.", show_alert=True); return
        if order["status"] != "taken":
            await callback.answer("❌ Ҳолат мос эмас.", show_alert=True); return
        await conn.execute("UPDATE orders SET status='done' WHERE id=$1", order_id)
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
    async with pool.acquire() as conn:
        drv = await conn.fetchrow("SELECT driver_id FROM drivers WHERE driver_id=$1", message.from_user.id)
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
            "✅ Тўловни амалга оширгандан кийин, квитанция (скриншот)ни шу ерга юборинг."
        )
        await message.answer(text, parse_mode="HTML")
    else:
        await message.answer("🔧 Админ учун: Баланс тўлдириш менюси.")

@router.message(F.photo)
async def handle_receipt_and_forward(message: Message):
    async with pool.acquire() as conn:
        drv = await conn.fetchrow("SELECT driver_id, username FROM drivers WHERE driver_id=$1", message.from_user.id)
    if not drv:
        return
    file_id = message.photo[-1].file_id
    async with pool.acquire() as conn:
        rec = await conn.fetchrow("INSERT INTO receipts(driver_id, file_id, status) VALUES($1,$2,'pending') RETURNING id", message.from_user.id, file_id)
        receipt_id = rec["id"] if rec else None
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
    # synchronous helper left as thin wrapper using pool (but not awaited)
    # prefer using async fetchrow directly where needed
    return None

@router.callback_query(F.data.startswith("approve_receipt:"))
async def approve_receipt_fixed(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Фақат админлар учун.", show_alert=True); return
    try:
        _, receipt_id_s, amount_s = callback.data.split(":")
        receipt_id = int(receipt_id_s); amount = int(amount_s)
    except Exception:
        await callback.answer("Нотўғри маълумот.", show_alert=True); return
    async with pool.acquire() as conn:
        rec = await conn.fetchrow("SELECT * FROM receipts WHERE id=$1", receipt_id)
        if not rec:
            await callback.answer("Квитанция топилмади.", show_alert=True); return
        if rec["status"] != "pending":
            await callback.answer("Квитанция аллақачон кўриб чиқилган.", show_alert=True); return
        await conn.execute("UPDATE receipts SET status='approved' WHERE id=$1", receipt_id)
        await conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + $1 WHERE driver_id=$2", amount, rec["driver_id"])
    try:
        await bot.send_message(rec["driver_id"], f"✅ Сиз юборган квитанция тасдиқланди. Балансингизга +{format_sum(amount)} сўм қўшилди.")
    except Exception:
        pass
    await callback.answer(f"✅ {format_sum(amount)} сўм — қўшилди.", show_alert=True)
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
    async with pool.acquire() as conn:
        rec = await conn.fetchrow("SELECT * FROM receipts WHERE id=$1", receipt_id)
        if not rec:
            await message.answer("Квитанция топилмади.")
            await state.clear()
            return
        if rec["status"] != "pending":
            await message.answer("Квитанция аллақачон кўриб чиқилган.")
            await state.clear()
            return
        await conn.execute("UPDATE receipts SET status='approved' WHERE id=$1", receipt_id)
        await conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + $1 WHERE driver_id=$2", amount, rec["driver_id"])
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
    async with pool.acquire() as conn:
        rec = await conn.fetchrow("SELECT * FROM receipts WHERE id=$1", receipt_id)
        if not rec:
            await callback.answer("Квитанция топилмади.", show_alert=True); return
        if rec["status"] != "pending":
            await callback.answer("Квитанция аллақачон кўриб чиқилган.", show_alert=True); return
        await conn.execute("UPDATE receipts SET status='rejected' WHERE id=$1", receipt_id)
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
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM orders ORDER BY id DESC LIMIT 50")
    if not rows:
        await message.answer("📭 Буюртмалар йўқ.")
        return
    for r in rows:
        await message.answer(format_order_row(r))

@router.message(F.text == "🚖 Ҳайдовчилар")
async def list_drivers_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM drivers ORDER BY driver_id DESC")
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE drivers SET status='blocked' WHERE driver_id=$1", driver_id)
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE drivers SET status='active' WHERE driver_id=$1", driver_id)
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
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM customers ORDER BY user_id DESC")
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE customers SET status='blocked' WHERE user_id=$1", user_id)
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE customers SET status='active' WHERE user_id=$1", user_id)
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

    async with pool.acquire() as conn:
        drivers = await conn.fetch("SELECT driver_id FROM drivers")
        customers = await conn.fetch("SELECT user_id FROM customers")

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

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT driver_id, username FROM drivers ORDER BY driver_id DESC")

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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + $1 WHERE driver_id=$2", amount, driver_id)

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

    async with pool.acquire() as conn:
        await conn.execute("UPDATE drivers SET balance = COALESCE(balance,0) + $1 WHERE driver_id=$2", amount, driver_id)

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
        [InlineKeyboardButton(text="👤 Developer", url="https://t.me/zaaaza13")],
        [InlineKeyboardButton(text="👤 Dilrabo", url="https://t.me/Rabo_logos")]
    ])
    await message.answer("📞 Админ билан боғланиш учун қуйидагилардан бирини танланг:", reply_markup=kb)

# --------------------------
# HOME
# --------------------------
@router.message(F.text == "🏠 Бош меню")
async def go_home(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("👑 Админ меню:", reply_markup=admin_menu_kb()); return
    async with pool.acquire() as conn:
        drv = await conn.fetchrow("SELECT * FROM drivers WHERE driver_id=$1", message.from_user.id)
    if drv:
        await message.answer("👋 Салом, ҳайдовчи!", reply_markup=driver_menu_kb())
    else:
        await message.answer("👋 Салом!", reply_markup=customer_menu_kb())

@router.message(F.text == "📝 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    async with pool.acquire() as conn:
        # Haydovchi profil
        driver = await conn.fetchrow("SELECT * FROM drivers WHERE driver_id=$1", user_id)
        if driver:
            text = (
                f"👤 <b>Ҳайдовчи профили</b>\n\n"
                f"🆔 ID: <code>{driver['driver_id']}</code>\n"
                f"👤 Исм: {driver['full_name'] or '—'}\n"
                f"📞 Телефон: {driver['phone'] or '—'}\n"
                f"🚗 Машина: {driver['car_model'] or '—'}\n"
                f"💳 Баланс: {format_sum(driver['balance'] or 0)} сўм\n"
                f"📌 Статус: {driver['status'] or '—'}\n"
            )
            await message.answer(text, reply_markup=driver_menu_kb())
            return

        # Mijoz profil
        customer = await conn.fetchrow("SELECT * FROM customers WHERE user_id=$1", user_id)
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

    await message.answer("❌ Сиз рўйхатдан ўтмагансиз!", reply_markup=role_kb())

# --------------------------
# START POLLING
# --------------------------
async def main():
    # init db and pool
    await init_db()
    print("🚀 Bot ишга тушди...")
    # start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
