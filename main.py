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
FAL_KEY         = os.getenv("FAL_KEY", "")  # fal.ai API key

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
    await msg.answer("Отправьте фото товара (лучше как *Файл/Документ*) — сделаю главное фото 3:4.")

@router.message(F.text == "📷 Фотосессия (12 снимков)")
async def set12(msg: Message):
    MODE[msg.chat.id] = "set"
    await msg.answer("Отправьте фото товара (лучше как *Файл/Документ*) — соберу 12 снимков в одном стиле.")

@router.message(F.text == "💬 Фейк-отзыв")
async def review(msg: Message):
    MODE[msg.chat.id] = "review"
    await msg.answer("Отправьте фото товара (пока верну 1 кадр; текст добавим позже).")

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

# ---------- Fal.ai (IMG2IMG) ----------
# ВАЖНО: используем именно dev IMG2IMG-эндпоинт
FAL_URL = "https://fal.run/fal-ai/flux/dev/image-to-image"  # schema: prompt, image_url, strength, steps, guidance, ...

PRESERVE_PREFIX = (
    "preserve the exact garment from the reference image: same color, print/pattern, fabric texture, "
    "stitching, silhouette and cut; do not alter the clothing design or details; no logo changes; "
)

NEGATIVE = (
    "different clothes, altered clothing, changed print, different color, wrong fabric, redesign, lingerie, text, logo, "
    "watermark, extra fingers, plastic skin, hdr glow, oversmooth"
)

def preset(mode: str) -> tuple[str, int]:
    if mode == "main":
        return (
            PRESERVE_PREFIX +
            "photorealistic ecommerce hero shot, mobile-photography look, soft daylight, clean warm bedroom, "
            "focus on garment details, aspect 3:4, high resolution, realistic skin",
            1
        )
    if mode == "set":
        return (
            PRESERVE_PREFIX +
            "photorealistic lifestyle photoshoot for fashion e-commerce, consistent style and lighting across shots, "
            "mix of full-body, 3/4, side, back, close-up fabric, minimal interior, soft shadows, aspect 3:4, high resolution",
            12
        )
    return (
        PRESERVE_PREFIX +
        "clean studio-like product modeling photo for marketplace, aspect 3:4, high resolution",
        1
    )

async def fal_img2img(image_url: str, mode: str, strength: float = 0.95) -> list[str]:
    prompt, n = preset(mode)
    payload = {
        "image_url": image_url,          # IMG2IMG
        "prompt": prompt,
        "strength": strength,            # В ЭТОМ эндпоинте высокие значения сильнее сохраняют исходник
        "num_inference_steps": 40,
        "guidance_scale": 3.2,
        "num_images": n,
        "enable_safety_checker": True,
        "output_format": "jpeg"
        # image_size здесь НЕТ (и не нужен)
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

# ---------- Достаём байты (photo/document) ----------
async def get_input_bytes(msg: Message) -> tuple[bytes, str]:
    if msg.photo:
        ph = msg.photo[-1]
        tg_file = await bot.get_file(ph.file_id)
        fs = await bot.download_file(tg_file.file_path)
        return fs.read(), ".jpg"
    if msg.document:
        tg_file = await bot.get_file(msg.document.file_id)
        fs = await bot.download_file(tg_file.file_path)
        suffix = ".jpg"
        name = (msg.document.file_name or "").lower()
        if name.endswith(".png"): suffix = ".png"
        elif name.endswith(".webp"): suffix = ".webp"
        elif name.endswith(".jpeg"): suffix = ".jpg"
        return fs.read(), suffix
    raise RuntimeError("Не найдено изображение: пришлите фото или файл-изображение.")

# ---------- Хендлер изображений ----------
@router.message(F.photo | F.document)
async def on_image(msg: Message):
    try:
        mode = MODE.get(msg.chat.id) or "main"
        await msg.answer("📥 Получил. Загружаю исходник в облако…")

        src_bytes, suffix = await get_input_bytes(msg)
        src_url = await sb_upload(src_bytes, suffix)
        await msg.answer("🧠 Генерирую через Fal.ai (img2img, сохранение вещи)…")

        gen_urls = await fal_img2img(src_url, mode, strength=0.95)

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

