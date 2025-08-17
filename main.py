import logging
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# Секреты — вставь свои значения
BOT_TOKEN = "7750454949:AAFflcDPKWKi4dfP6CO3zZjKY-FN04PXc-A"
FAL_KEY = "f0270158-70e8-4a20-b0ef-65a081b0dd23:78d5f3acea93e416107736767832eec7"

# Настройки
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# fal.ai эндпоинт
FAL_URL = "https://fal.run/fal-ai/flux-pro"

# Пресеты под разные кнопки
def presets_for(mode: str) -> tuple[str, int]:
    if mode == "main":
        return (
            "photorealistic ecommerce hero shot, mobile-photography, soft daylight, clean warm bedroom, "
            "focus on garment details, 3:4 aspect ratio, high resolution, no watermark, no logos, realistic skin",
            1
        )
    if mode == "set":
        return (
            "photorealistic lifestyle photoshoot for fashion e-commerce, consistent style and lighting, "
            "mix of full-body, 3/4, side, back, close-up fabric, minimal interior, 3:4 aspect ratio, high resolution, no watermark",
            12
        )
    return (
        "clean studio-like product modeling photo for marketplace, 3:4 aspect ratio, high resolution, no watermark, no logos",
        1
    )

# Генерация через fal.ai
async def fal_generate(image_url: str, mode: str) -> list[str]:
    prompt, num_images = presets_for(mode)
    payload = {
        "input": {
            "image_url": image_url,
            "prompt": prompt,
            "num_images": num_images,
            "strength": 0.45,
            "guidance_scale": 4.0,
            "image_size": "3072x4096",
            "negative_prompt": "watermark, text, logo, extra fingers, plastic skin, hdr glow, oversmooth"
        }
    }
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(FAL_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    images = data.get("images") or data.get("output", {}).get("images") or []
    if not images:
        raise RuntimeError(f"Fal response has no images: {data}")

    return [img["url"] if isinstance(img, dict) else img for img in images]

# Хендлер для кнопки "Главное фото"
@dp.message_handler(lambda m: m.text == "📸 Главное фото")
async def cmd_main_photo(message: types.Message):
    await message.answer("Отправьте фото товара — сделаю главное фото (3:4, студийный стиль).")
    dp.register_message_handler(process_main_photo, content_types=["photo"], state=None)

async def process_main_photo(message: types.Message):
    await message.answer("📤 Фото получено. Загружаю в облако...")

    # Получаем file_url из Telegram
    file_info = await bot.get_file(message.photo[-1].file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

    await message.answer("🧠 Генерирую через Fal.ai...")
    try:
        images = await fal_generate(file_url, "main")
        for url in images:
            await message.answer_photo(photo=url)
    except Exception as e:
        await message.answer(f"⚠️ Fal.ai вернул ошибку: {e}")

    dp.unregister_message_handler(process_main_photo, content_types=["photo"], state=None)

# Старт
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)


