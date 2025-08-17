import os
import time
import uuid
import httpx

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update

# ---------- ENV (Railway Variables) ----------
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "wb-photos")
FAL_KEY         = os.getenv("FAL_KEY", "")  # ключ fal.ai

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not (SUPABASE_URL and SUPABASE_KEY and SUPABASE_BUCKET):
    raise RuntimeError("Supabase env vars missing")
if not FAL_KEY:
    raise RuntimeError("FAL_KEY is not set")

# ---------- Telegram ----------
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

MODE: dict[int, str | None] = {}

@router.message(Command("start"))
async def start_cmd(msg: Message):
    MODE[msg.chat.id] = None
    await msg.answer("Привет! Выберите действие:", reply_markup=menu_kb)

@router.message(F.text == "📸 Главное фото")
async def main_photo(msg: Message):
    MODE[msg.chat.id] = "main"
    await msg.answer("Отправьте фото товара — сделаю главное фото (3:4).")

@router.message(F.text == "📷 Фотосессия (12 снимков)")
async def set12(msg: Message):
    MODE[msg.chat.id] = "set"
    await msg.answer("Отправьте фото товара — соберу 12 снимков в одном стиле.")

@router.message(F.text == "💬 Фейк-отзыв")
async def review(msg: Message):
    MODE[msg.chat.id] = "review"
    await msg.answer("Отправьте фото товара — пока верну 1 кадр (текст добавим позже).")

# ---------- Supabase ----------
def _public_url(path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"

async def sb_upload(content: bytes, suffix: str = ".jpg") -> str:
    name = f"uploads/{int(time.time())}-{uuid.uuid4().hex}{suffix}"
    url  = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{name}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/octet-stream",
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, headers=headers, content=content)
        r.raise_for_status()
    return _public_url(name)

# ---------- Fal.ai (img2img через flux-pro) ----------
FAL_URL = "https://fal.run/fal-ai/flux-pro"

def preset(mode: str) -> tuple[str, int]:
    if mode == "main":
        return ("photorealistic ecommerce hero shot, soft daylight, clean warm bedroom,"
                " focus on garment details, 3:4, high resolution, no watermark, realistic skin", 1)
    if mode == "set":
        return ("photorealistic lifestyle photoshoot for fashion e-commerce, consistent lighting,"
                " mix of full-body, 3/4, side, back, close-up fabric, minimal interior, 3:4, high resolution,"
                " no watermark", 12)
    return ("clean studio-like product modeling photo for marketplace, 3:4, high resolution, no watermark", 1)

async def fal_img2img(image_url: str, mode: str) -> list[str]:
    p, n = preset(mode)
    payload = {
        # В fal.ai наличие image_url включает img2img
        "prompt": p,
        "image_url": image_url,
        "num_images": n,
        "strength": 0.45,                 # бережно к ткани
        "guidance_scale": 4.0,
        # === ФИКС: допускаются только предустановки размера ===
        # square_hd | square | portrait_4_3 | portrait_16_9 | landscape_4_3 | landscape_16_9
        "image_size": "portrait_4_3",
        "negative_prompt": "watermark, text, logo, extra fingers, plastic skin, hdr glow, oversmooth"
    }
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(FAL_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    images = data.get("images") or data.get("output", {}).get("images") or []
    if not images:
        raise RuntimeError(f"Fal response has no images: {data}")
    return [img["url"] if isinstance(img, dict) else img for img in images]

# ---------- Фото-хендлер ----------
@router.message(F.photo)
async def on_photo(msg: Message):
    try:
        mode = MODE.get(msg.chat.id) or "main"
        await msg.answer("📥 Фото получено. Загружаю в облако…")

        ph = msg.photo[-1]
        tg_file = await bot.get_file(ph.file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        src_bytes = file_stream.read()

        src_url = await sb_upload(src_bytes, ".jpg")
        await msg.answer("🧠 Генерирую через Fal.ai…")

        gen_urls = await fal_img2img(src_url, mode)

        if mode == "set" and len(gen_urls) > 1:
            links = []
            async with httpx.AsyncClient(timeout=180) as c:
                for u in gen_urls:
                    content = (await c.get(u)).content
                    links.append(await sb_upload(content, ".jpg"))
            await msg.answer("✅ Готово. 12 ссылок:\n" + "\n".join(links))
        else:
            async with httpx.AsyncClient(timeout=180) as c:
                content = (await c.get(gen_urls[0])).content
            out_url = await sb_upload(content, ".jpg")
            await msg.answer_photo(photo=out_url, caption=f"✅ Готово. Ссылка: {out_url}")

    except httpx.HTTPStatusError as e:
        await msg.answer(f"⚠️ Fal.ai {e.response.status_code}: {e.response.text[:400]}")
    except Exception as e:
        await msg.answer(f"⚠️ Ошибка: {e}")

dp.include_router(router)

# ---------- FastAPI + Webhook ----------
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.post("/webhook")
async def webhook(request: Request):
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"ok": True}
