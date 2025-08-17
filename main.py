import os
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Update

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- Меню кнопками ---
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📸 Главное фото")],
        [KeyboardButton(text="📷 Фотосессия (12 снимков)")],
        [KeyboardButton(text="💬 Фейк-отзыв")],
    ],
    resize_keyboard=True
)

@router.message(Command("start"))
async def start_cmd(msg: Message):
    await msg.answer("Привет! 👋 Выберите действие:", reply_markup=menu_kb)

@router.message(F.text == "📸 Главное фото")
async def main_photo(msg: Message):
    await msg.answer("Отправьте фото товара, я сделаю главное фото.")

@router.message(F.text == "📷 Фотосессия (12 снимков)")
async def photoset(msg: Message):
    await msg.answer("Отправьте фото товара, я соберу фотосессию из 12 снимков.")

@router.message(F.text == "💬 Фейк-отзыв")
async def fake_review(msg: Message):
    await msg.answer("Отправьте фото товара, и я сгенерирую отзыв.")

dp.include_router(router)

# --- FastAPI часть для вебхука ---
app = FastAPI()

@app.post("/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"ok": True}
