import os
import time
import uuid
import httpx

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Update,
)

# --------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "wb-photos")

# --------- TG ----------
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()

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

def _public_url(object_path: str) -> str:
    # Для public-бакета Supabase публичная ссылка формируется так:
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{object_path}"

async def upload_to_supabase(file_bytes: bytes, suffix: str = ".jpg") -> str:
    """Загрузка файла в Supabase Storage. Возвращает публичную ссылку."""
    if not (SUPABASE_URL and SUPABASE_KEY and SUPABASE_BUCKET):
        raise RuntimeError("Supabase env vars missing (SUPABASE_URL/KEY/BUCKET)")
    filename = f"{int(time.time())}-{uuid.uuid4().hex}{suffix}"
    object_path = f"uploads/{filename}"
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{object_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, content=file_bytes)
        r.raise_for_status()
    return _public_url(object_path)

@router.message(F.photo)
async def on_photo(msg: Message):
    """Принимаем фото → грузим в Supabase → возвращаем ссылку."""
    try:
        await msg.answer("📥 Фото получено. Загружаю в облако…")
        ph = msg.photo[-1]  # самое крупное превью
        tg_file = await bot.get_file(ph.file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        img_bytes = file_stream.read()

        public_link = await upload_to_supabase(img_bytes, suffix=".jpg")
        await msg.answer(f"✅ Залил. Ссылка: {public_link}")
        # Дальше тут будет вызов генерации (картинки/текст) — позже добавим.
    except Exception as e:
        await msg.answer(f"⚠️ Ошибка загрузки: {e}")

dp.include_router(router)

# --------- FastAPI ----------
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

