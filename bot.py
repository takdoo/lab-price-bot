import os
import json
import requests
import jdatetime

from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "@lab_chats")
MESSAGE_ID = os.environ.get("MESSAGE_ID", "")

API = "https://api.coingecko.com/api/v3/simple/price"
BASE_IMAGE = "lab-price-bot.png"
OUTPUT_IMAGE = "lab_live.jpg"


def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]

    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def create_image(price_text):
    image = Image.open(BASE_IMAGE).convert("RGBA")
    width, height = image.size

    # متن قیمت
    font_size = int(width * 0.105)
    font = get_font(font_size)

    x = width // 2
    y = int(height * 0.48)

    # افکت نور سبز
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)

    glow_draw.text(
        (x, y),
        price_text,
        font=font,
        anchor="mm",
        fill=(80, 255, 100, 180),
        stroke_width=3
    )

    glow = glow.filter(ImageFilter.GaussianBlur(14))
    image = Image.alpha_composite(image, glow)

    # نوشته اصلی
    draw = ImageDraw.Draw(image)
    draw.text(
        (x, y),
        price_text,
        font=font,
        anchor="mm",
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(15, 50, 25, 255)
    )

    image.convert("RGB").save(
        OUTPUT_IMAGE,
        quality=95,
        optimize=True
    )


def main():
    response = requests.get(
        API,
        params={
            "ids": "lab",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        },
        timeout=25
    )
    response.raise_for_status()

    lab = response.json()["lab"]

    price = float(lab["usd"])
    change = float(lab["usd_24h_change"])

    price_text = f"${price:.6f}"

    create_image(price_text)

    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)

    if change > 0:
        movement = f"🟢 📈 <b>+{change:.2f}%</b>"
        status = "🟢 بازار صعودی"
    elif change < 0:
        movement = f"🔴 📉 <b>{change:.2f}%</b>"
        status = "🔴 بازار نزولی"
    else:
        movement = "🟡 ➖ <b>0.00%</b>"
        status = "🟡 بدون تغییر محسوس"

    caption = (
        "🧪 <b>LAB PREMIUM LIVE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>قیمت LAB:</b> <code>{price_text}</code>\n"
        f"📊 <b>تغییر ۲۴ ساعته:</b> {movement}\n"
        f"📌 <b>وضعیت:</b> {status}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>زمان ایران:</b> "
        f"{jalali.strftime('%Y/%m/%d - %H:%M:%S')}\n"
        "🔄 بروزرسانی خودکار هر ۵ دقیقه\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>گروه:</b> @lab_chats\n"
        "👨‍💻 <b>طراح و توسعه:</b> علی حامدی"
    )

    with open(OUTPUT_IMAGE, "rb") as photo:

        if MESSAGE_ID:
            media = {
                "type": "photo",
                "media": "attach://photo",
                "caption": caption,
                "parse_mode": "HTML"
            }

            result = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/editMessageMedia",
                data={
                    "chat_id": CHAT_ID,
                    "message_id": MESSAGE_ID,
                    "media": json.dumps(media)
                },
                files={"photo": photo},
                timeout=40
            )

        else:
            result = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={"photo": photo},
                timeout=40
            )

    result.raise_for_status()
    answer = result.json()

    if not answer.get("ok"):
        raise RuntimeError(answer)

    if not MESSAGE_ID:
        print("MESSAGE_ID =", answer["result"]["message_id"])

    print("LAB IMAGE UPDATED:", price_text)


if __name__ == "__main__":
    main()
