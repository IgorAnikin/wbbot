import os
import time
import uuid
import httpx

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update

# ---------- ENV (в Railway уже есть) ----------
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "wb-photos")
FAL_KEY         = os.getenv("FAL_KEY", "")  # <- ДОБАВЬ в Railway

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not (SUPABASE_URL and SUPABASE_KEY and SUPABASE_BUCKET):
    raise RuntimeError("Supabase env vars missing")
if not FAL_KEY:
    raise RuntimeError("FAL_KEY is not set")

# ---------- TG ----------
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

# Запоминаем текущий режим (простая память на чат)
MODE_BY_CHAT = {}  # chat_id -> "main" | "set" | "review"

@router.message(Command("start"))
async def start_cmd(msg: Message):
    MODE_BY_CHAT[msg.chat.id] = None
    await msg.answer("Привет! 👋 Выберите действие:", reply_markup=menu_kb)

@router.message(F.text == "📸 Главное фото")
async def main_photo(msg: Message):
    MODE_BY_CHAT[msg.chat.id] = "main"
    await msg.answer("Отправьте фото товара — сделаю главное фото (3:4, студийный стиль).")

@router.message(F.text == "📷 Фотосессия (12 снимков)")
async def photoset(msg: Message):
    MODE_BY_CHAT[msg.chat.id] = "set"
    await msg.answer("Отправьте фото товара — соберу серию из 12 снимков в едином стиле.")

@router.message(F.text == "💬 Фейк-отзыв")
async def fake_review(msg: Message):
    MODE_BY_CHAT[msg.chat.id] = "review"
    await msg.answer("Отправьте фото товара — сгенерирую текст отзыва (сейчас верну только картинку как заглушку).")

# ---------- Supabase ----------
def _public_url(object_path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{object_path}"

async def upload_to_supabase(file_bytes: bytes, suffix: str = ".jpg") -> str:
    filename = f"{int(time.time())}-{uuid.uuid4().hex}{suffix}"
    object_path = f"uploads/{filename}"
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{object_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/octet-stream",
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, content=file_bytes)
        r.raise_for_status()
    return _public_url(object_path)

# ---------- Fal.ai ----------
async def fal_generate(image_url: str, mode: str) -> str:
    """
    Делает img2img через Fal.ai и возвращает URL сгенерированной картинки.
    Мы используем единый endpoint flux-pro (синхронный вызов).
    """
    # Мастер-промпты под кнопки
    if mode == "main":
        prompt = (
            "photorealistic ecommerce hero shot, mobile-photography aesthetic, soft daylight, "
            "clean minimalist warm bedroom, mirror selfie framing optional, focus on garment details, "
            "3:4 aspect ratio, high resolution, no watermark, no logos, realistic skin"
        )
        num_images = 1
    elif mode == "set":
        prompt = (
            "photorealistic lifestyle photoshoot for fashion e-commerce, consistent style and lighting, "
            "mix of full-body, 3/4, side, back, close-up fabric, minimal interior, soft shadow, "
            "3:4 aspect ratio, high resolution, no watermark, no logos"
        )
        num_images = 12
    else:  # "review" — пока делаем один кадр-заглушку под отзыв
        prompt = (
            "clean studio-like product modeling photo for marketplace, 3:4 aspect ratio, high resolution, "
            "no watermark, no logos"
        )
        num_images = 1

    # Параметры «бережного» img2img (держим ткань и посадку)
    payload = {
        "image_url": image_url,          # наша реф-картинка из Supabase (публичная)
        "prompt": prompt,
        "num_images": num_images,
        # Ниже — параметры, поддерживаются большинством flux/SDXL-compatible роутов на Fal.
        "guidance_scale": 4.0,
        "strength": 0.45,                # низкий denoise, чтобы сохранить ткань
        "image_size": "3072x4096",       # 3:4
        "negative_prompt": "watermark, text, logo, extra fingers, plastic skin, hdr glow, oversmooth"
    }

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json"
    }

    # Синхронный (blocking) вызов — Fal вернёт итог сразу.
    # Примечание: у некоторых моделей Fal просит другое имя роутера.
    # Этот работает как общий img2img на базе Flux.
    fal_url = "https://fal.run/fal-ai/flux-pro-img2img"

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(fal_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    # Ожидаем поле images -> [ { url: ... } ]
    images = data.get("images") or []
    if not images:
        raise RuntimeError(f"Fal response has no images: {data}")
    # Если 12 снимков — вернём первый url (ниже отправим все)
    return [img["url"] for img in images]

# ---------- Фото-хендлер ----------
@router.message(F.photo)
async def on_photo(msg: Message):
    try:
        mode = MODE_BY_CHAT.get(msg.chat.id) or "main"
        await msg.answer("📥 Фото получено. Загружаю в облако…")
        # Берём самое большое превью
        ph = msg.photo[-1]
        tg_file = await bot.get_file(ph.file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        img_bytes = file_stream.read()

        # 1) сохраняем исходник в Supabase и получаем публичный URL
        src_url = await upload_to_supabase(img_bytes, suffix=".jpg")
        await msg.answer("🧠 Генерирую через Fal.ai…")

        # 2) генерим
        gen_urls = await fal_generate(src_url, mode=mode)

        # 3) отдаём результат(ы)
        if mode == "set" and len(gen_urls) > 1:
            # Загружаем все 12 снимков обратно в Supabase и шлём ссылки
            links = []
            async with httpx.AsyncClient(timeout=120) as client:
                for u in gen_urls:
                    content = (await client.get(u)).content
                    out_url = await upload_to_supabase(content, suffix=".jpg")
                    links.append(out_url)
            text = "✅ Готово. 12 снимков:\n" + "\n".join(links)
            await msg.answer(text)
        else:
            # Один кадр
            async with httpx.AsyncClient(timeout=120) as client:
                content = (await client.get(gen_urls[0])).content
            out_url = await upload_to_supabase(content, suffix=".jpg")
            await msg.answer_photo(photo=out_url, caption=f"✅ Готово. Ссылка: {out_url}")

    except Exception as e:
        await msg.answer(f"⚠️ Ошибка: {e}")

dp.include_router(router)

# ---------- FastAPI (вебхук) ----------
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.post("/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

