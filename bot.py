import os
import json
import requests
import jdatetime

from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "@lab_chats")
MESSAGE_ID = os.environ.get("MESSAGE_ID", "")

BASE_IMAGE = "lab-price-bot.png"
OUTPUT_IMAGE = "lab_live.jpg"

API = "https://api.coingecko.com/api/v3"


def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def get_market_data():
    r = requests.get(
        f"{API}/simple/price",
        params={
            "ids": "lab",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        },
        timeout=25
    )
    r.raise_for_status()
    lab = r.json()["lab"]

    price = float(lab["usd"])
    change = float(lab["usd_24h_change"])

    history = []
    try:
        h = requests.get(
            f"{API}/coins/lab/market_chart",
            params={"vs_currency": "usd", "days": "1"},
            timeout=25
        )
        h.raise_for_status()
        history = [
            float(item[1])
            for item in h.json().get("prices", [])
        ]
    except Exception as e:
        print("Historical chart unavailable:", e)

    return price, change, history


def draw_chart(draw, points, color, x1, y1, x2, y2):
    if len(points) < 2:
        draw.text(
            ((x1 + x2) // 2, (y1 + y2) // 2),
            "CHART UNAVAILABLE",
            font=get_font(13),
            fill=(210, 210, 210),
            anchor="mm"
        )
        return

    low = min(points)
    high = max(points)

    if high == low:
        high = low + 0.000001

    coords = []
    count = len(points)

    for i, value in enumerate(points):
        x = x1 + (x2 - x1) * i / (count - 1)
        y = y2 - (value - low) / (high - low) * (y2 - y1)
        coords.append((int(x), int(y)))

    draw.line(coords, fill=color, width=4, joint="curve")


def create_image(price_text, change, history):
    image = Image.open(BASE_IMAGE).convert("RGBA")
    width, height = image.size

    draw = ImageDraw.Draw(image)

    # قیمت اصلی
    price_font = get_font(int(width * 0.105))
    draw.text(
        (width // 2, int(height * 0.48)),
        price_text,
        font=price_font,
        anchor="mm",
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(15, 50, 25, 255)
    )

    # محدوده دو پنل پایین
    left_x1 = int(width * 0.10)
    left_x2 = int(width * 0.47)
    right_x1 = int(width * 0.53)
    right_x2 = int(width * 0.90)

    top = int(height * 0.70)

    small_font = get_font(int(width * 0.027))
    value_font = get_font(int(width * 0.050))

    green = (70, 255, 120, 255)
    red = (255, 75, 85, 255)
    white = (245, 245, 245, 255)

    # پنل سبز
    draw.text(
        ((left_x1 + left_x2) // 2, top),
        "24H GAIN",
        font=small_font,
        fill=green if change > 0 else white,
        anchor="mm"
    )

    if change > 0:
        green_value = f"+{change:.2f}%"
        draw.text(
            ((left_x1 + left_x2) // 2, int(height * 0.77)),
            green_value,
            font=value_font,
            fill=green,
            anchor="mm"
        )
        draw_chart(
            draw, history, green,
            left_x1, int(height * 0.82),
            left_x2, int(height * 0.90)
        )
    else:
        draw.text(
            ((left_x1 + left_x2) // 2, int(height * 0.79)),
            "INACTIVE",
            font=small_font,
            fill=white,
            anchor="mm"
        )

    # پنل قرمز
    draw.text(
        ((right_x1 + right_x2) // 2, top),
        "24H LOSS",
        font=small_font,
        fill=red if change < 0 else white,
        anchor="mm"
    )

    if change < 0:
        red_value = f"{change:.2f}%"
        draw.text(
            ((right_x1 + right_x2) // 2, int(height *
        0.77)),
            red_value,
            font=value_font,
            fill=red,
            anchor="mm"
        )
        draw_chart(
            draw, history, red,
            right_x1, int(height * 0.82),
            right_x2, int(height * 0.90)
        )
    else:
        draw.text(
            ((right_x1 + right_x2) // 2, int(height * 0.79)),
            "INACTIVE",
            font=small_font,
            fill=white,
            anchor="mm"
        )

    image.convert("RGB").save(
        OUTPUT_IMAGE,
        quality=95
    )


def main():
    price, change, history = get_market_data()

    price_text = f"${price:.6f}"

    create_image(price_text, change, history)

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
        "🔄 بروزرسانی هر ۵ دقیقه\n"
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
