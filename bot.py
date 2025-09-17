# -*- coding: utf-8 -*-
"""
cargobot_updated.py
To'liq ishlaydigan bot (asyncpg + PostgreSQL, Railway uchun)
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
# SETTINGS
# --------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = {1262207928, 8011859232}

# --------------------------
# BOT INIT
# --------------------------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --------------------------
# DATABASE
# --------------------------
pool: asyncpg.Pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
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
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]],
        resize_keyboard=True
    )

# --------------------------
# STATES
# --------------------------
class CustomerReg(StatesGroup):
    waiting_for_phone = State()
    waiting_for_fullname = State()

class DriverRegistration(StatesGroup):
    ask_phone = State()
    ask_fullname = State()
    ask_car_model = State()
    custom_car = State()

class NewOrder(StatesGroup):
    from_address = State()
    to_address = State()
    cargo_type = State()
    car_type = State()
    cargo_weight = State()
    ask_phone = State()

# --------------------------
# HELPERS
# --------------------------
async def list_active_driver_ids():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT driver_id FROM drivers WHERE status='active'")
        return [r["driver_id"] for r in rows]

async def push_new_order_to_drivers(order_row):
    fee = order_row["commission"] or 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул қилиш", callback_data=f"accept:{order_row['id']}"),
         InlineKeyboardButton(text="❌ Рад этиш", callback_data=f"reject:{order_row['id']}")]
    ])
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
        await message.answer("👑 Админ интерфейс:", reply_markup=admin_menu_kb())
    elif driver:
        if driver["status"] == "blocked":
            await message.answer("❗ Сиз блоклангансиз.")
            return
        await message.answer("👋 Салом, ҳайдовчи!", reply_markup=driver_menu_kb())
    elif customer:
        await message.answer("👋 Салом, мижоз!", reply_markup=customer_menu_kb())
    else:
        await message.answer("👋 Салом! Илтимос, ролингизни танланг:", reply_markup=role_kb())

# --------------------------
# ROLE HANDLERS (misol uchun mijoz)
# --------------------------
@router.message(F.text == "👤 Мижоз")
async def role_customer(message: Message):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO customers(user_id, username, phone, status)
            VALUES($1, $2, $3, 'active')
            ON CONFLICT (user_id) DO NOTHING
        """, message.from_user.id, f"@{message.from_user.username}" if message.from_user.username else None, None)
    await message.answer("✅ Сиз мижоз сифатида рўйхатдан ўтдингиз!", reply_markup=customer_menu_kb())

# --------------------------
# ADMIN BUYURTMALAR
# --------------------------
@router.message(F.text == "📊 Барча буюртмалар")
async def all_orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM orders ORDER BY id DESC LIMIT 20")
    if not rows:
        await message.answer("📭 Буюртмалар йўқ.")
        return
    for r in rows:
        await message.answer(
            f"🆔 {r['id']} | {r['date']}\n"
            f"📍 {r['from_address']} ➜ {r['to_address']}\n"
            f"📦 {r['cargo_type']} | 🚘 {r['car_type']}\n"
            f"⚖️ {r['cargo_weight']} кг | 💵 {r['commission'] or '—'}\n"
            f"📌 Статус: {r['status']}"
        )

# --------------------------
# START POLLING
# --------------------------
async def main():
    await init_db()
    print("🚀 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
