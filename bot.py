import os
import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import jdatetime
from PIL import Image, ImageDraw, ImageFont


# ---------------- تنظیمات ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "@lab_chats")
MESSAGE_ID = os.getenv("MESSAGE_ID")

BASE_IMAGE = "lab-price-bot.png"
OUTPUT_IMAGE = "lab_live.jpg"

PRICE_API = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=lab&vs_currencies=usd&include_24hr_change=true"
)

HISTORY_API = (
    "https://api.coingecko.com/api/v3/coins/lab/market_chart"
    "?vs_currency=usd&days=1"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# ---------------- فونت ----------------

def get_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)

    return ImageFont.load_default()


# ---------------- دریافت قیمت ----------------

def get_market_data():
    response = requests.get(PRICE_API, timeout=20)
    response.raise_for_status()

    data = response.json()

    if "lab" not in data or "usd" not in data["lab"]:
        raise ValueError("قیمت LAB در پاسخ دریافت نشد")

    price = float(data["lab"]["usd"])
    change = float(data["lab"].get("usd_24h_change", 0))

    return price, change


# ---------------- دریافت نمودار ۲۴ ساعته ----------------

def get_history():
    try:
        response = requests.get(HISTORY_API, timeout=20)
        response.raise_for_status()

        data = response.json()
        prices = data.get("prices", [])

        return [float(item[1]) for item in prices]

    except Exception as error:
        logging.warning("دریافت نمودار تاریخی ناموفق بود: %s", error)
        return []


# ---------------- رسم نمودار ----------------

def draw_chart(draw, prices, x1, y1, x2, y2, color):
    if len(prices) < 2:
        return

    minimum = min(prices)
    maximum = max(prices)

    if maximum == minimum:
        return

    points = []

    for index, price in enumerate(prices):
        x = x1 + (index / (len(prices) - 1)) * (x2 - x1)
        y = y2 - ((price - minimum) / (maximum - minimum)) * (y2 - y1)
        points.append((int(x), int(y)))

    draw.line(points, fill=color, width=4, joint="curve")


# ---------------- ساخت عکس ----------------

def create_image(price, change, history):
    if not os.path.exists(BASE_IMAGE):
        raise FileNotFoundError(
            f"عکس اصلی پیدا نشد: {BASE_IMAGE}"
        )

    image = Image.open(BASE_IMAGE).convert("RGB")
    draw = ImageDraw.Draw(image)

    width, height = image.size

    white = (255, 255, 255)
    green = (35, 230, 130)
    red = (255, 75, 90)
    muted = (180, 190, 200)

    # قیمت با ۵ رقم اعشار
    price_text = f"${price:.5f}"
    price_font = get_font(max(42, int(width * 0.105)))

    draw.text(
        (width // 2, int(height * 0.48)),
        price_text,
        font=price_font,
        fill=white,
        anchor="mm",
        stroke_width=1,
        stroke_fill=(0, 0, 0)
    )

    # محدوده پنل‌ها
    left_x1 = int(width * 0.10)
    left_x2 = int(width * 0.47)

    right_x1 = int(width * 0.53)
    right_x2 = int(width * 0.90)

    value_font = get_font(max(18, int(width * 0.035)))
    dash_font = get_font(max(20, int(width * 0.04)))

    # پنل مثبت
    if change >= 0:
        green_text = f"+{change:.2f}%"

        draw.text(
            ((left_x1 + left_x2) // 2, int(height * 0.77)),
            green_text,
            font=value_font,
            fill=green,
            anchor="mm"
        )

        draw_chart(
            draw,
            history,
            left_x1,
            int(height * 0.82),
            left_x2,
            int(height * 0.90),
            green
        )

        draw.text(
            ((right_x1 + right_x2) // 2, int(height * 0.79)),
            "—",
        font=dash_font,
            fill=muted,
            anchor="mm"
        )

    # پنل منفی
    else:
        red_text = f"{change:.2f}%"

        draw.text(
            ((left_x1 + left_x2) // 2, int(height * 0.79)),
            "—",
            font=dash_font,
            fill=muted,
            anchor="mm"
        )

        draw.text(
            ((right_x1 + right_x2) // 2, int(height * 0.80)),
            red_text,
            font=value_font,
            fill=red,
            anchor="mm"
        )

        draw_chart(
            draw,
            history,
            right_x1,
            int(height * 0.82),
            right_x2,
            int(height * 0.90),
            red
        )

    image.save(OUTPUT_IMAGE, "JPEG", quality=95)
    return OUTPUT_IMAGE


# ---------------- تاریخ و ساعت ایران ----------------

def get_iran_time():
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)

    date_text = jalali.strftime("%Y/%m/%d")
    time_text = now.strftime("%H:%M:%S")

    return date_text, time_text


# ---------------- متن فارسی تلگرام ----------------

def make_caption(price, change):
    date_text, time_text = get_iran_time()

    if change > 0:
        status = "🟢 بازار امروز مثبته"
        change_text = f"+{change:.2f}%"
    elif change < 0:
        status = "🔴 بازار امروز منفیه"
        change_text = f"{change:.2f}%"
    else:
        status = "⚪ بازار بدون تغییره"
        change_text = "0.00%"

    return (
        "🧪 <b>گزارش لحظه‌ای قیمت LAB</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 <b>قیمت: ${price:.5f}</b>\n"
        f"📊 <b>تغییر ۲۴ ساعته: {change_text}</b>\n"
        f"{status}\n"
        "━━━━━━━━━━━━━━\n"
        f"📅 <b>تاریخ: {date_text}</b>\n"
        f"🕒 <b>ساعت ایران: {time_text}</b>\n"
        "🔄 <b>بروزرسانی خودکار هر ۵ دقیقه</b>\n\n"
        "👨‍💻 <b>طراح و سازنده: علی حامدی</b>\n"
        "👥 <b>گروه تلگرامی: @lab_chats</b>"
    )


# ---------------- ارسال یا ویرایش پیام ----------------

def send_to_telegram(image_path, caption):
    if not BOT_TOKEN:
        raise ValueError("توکن ربات در Secrets تنظیم نشده است")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/"

    with open(image_path, "rb") as photo:
        if MESSAGE_ID:
            media = {
                "type": "photo",
                "media": "attach://photo",
                "caption": caption,
                "parse_mode": "HTML"
            }

            response = requests.post(
                url + "editMessageMedia",
                data={
                    "chat_id": CHAT_ID,
                    "message_id": MESSAGE_ID,
                    "media": json.dumps(media)
                },
                files={"photo": photo},
                timeout=30
            )

        else:
            response = requests.post(
                url + "sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={"photo": photo},
                timeout=30
            )

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            "خطای تلگرام: " + str(result.get("description"))
        )

    logging.info("پیام LAB با موفقیت بروزرسانی شد")


# ---------------- اجرای اصلی ----------------

def main():
    logging.info("ربات قیمت LAB شروع به کار کرد")

    price, change = get_market_data()
    history = get_history()

    image_path = create_image(price, change, history)
    caption = make_caption(price, change)

    send_to_telegram(image_path, caption)

    logging.info(
        "قیمت LAB: %.5f | تغییر ۲۴ ساعته: %.2f%%",
        price,
        change
    )


if __name__ == "__main__":
    main()
