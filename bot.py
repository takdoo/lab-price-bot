import os
import json
import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import jdatetime
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "@lab_chats").strip()
MESSAGE_ID = (os.getenv("MESSAGE_ID") or "").strip()
BASE_IMAGE = "lab-price-bot.png"
OUTPUT_IMAGE = "lab_live.jpg"
PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=lab&vs_currencies=usd&include_24hr_change=true&include_last_updated_at=true"
HISTORY_API = "https://api.coingecko.com/api/v3/coins/lab/market_chart?vs_currency=usd&days=1"
MAX_PRICE_AGE_SECONDS = 15 * 60
REQUEST_TIMEOUT = (10, 25)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def build_session():
    retry = Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "LAB-Price-Bot/2.0"})
    return session


HTTP = build_session()


def get_font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def fit_font(draw, text, max_width, initial_size, min_size=16):
    size = max(min_size, int(initial_size))
    while size > min_size:
        font = get_font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
        if bounds[2] - bounds[0] <= max_width:
            return font
        size -= 2
    return get_font(min_size)


def get_market_data():
    response = HTTP.get(PRICE_API, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    coin = data.get("lab")
    if not isinstance(coin, dict):
        raise ValueError("CoinGecko پاسخ LAB را برنگرداند؛ شناسه ارز را بررسی کنید")

    raw_price = coin.get("usd")
    if raw_price is None:
        raise ValueError("قیمت LAB در پاسخ CoinGecko وجود ندارد")
    price = float(raw_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"قیمت نامعتبر دریافت شد: {raw_price!r}")

    raw_change = coin.get("usd_24h_change")
    change = None if raw_change is None else float(raw_change)
    if change is not None and not math.isfinite(change):
        change = None

    updated_at = coin.get("last_updated_at")
    if updated_at:
        age = datetime.now(timezone.utc).timestamp() - float(updated_at)
        if age > MAX_PRICE_AGE_SECONDS:
            raise ValueError(f"قیمت دریافتی قدیمی است ({int(age)} ثانیه)؛ انتشار متوقف شد")
    return price, change


def get_history():
    response = HTTP.get(HISTORY_API, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    prices = response.json().get("prices")
    if not isinstance(prices, list):
        raise ValueError("ساختار تاریخچه قیمت نامعتبر است")

    clean = []
    for item in prices:
        try:
            value = float(item[1])
            if math.isfinite(value) and value > 0:
                clean.append(value)
        except (TypeError, ValueError, IndexError):
            continue
    if len(clean) < 2:
        raise ValueError("داده کافی برای نمودار ۲۴ ساعته دریافت نشد")
    return clean


def draw_chart(draw, prices, x1, y1, x2, y2, color):
    if len(prices) < 2:
        return
    minimum, maximum = min(prices), max(prices)
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return
    points = []
    spread = maximum - minimum
    for index, price in enumerate(prices):
        x = x1 + (index / (len(prices) - 1)) * (x2 - x1)
        y = (y1 + y2) / 2 if spread == 0 else y2 - ((price - minimum) / spread) * (y2 - y1)
        points.append((int(x), int(y)))
    draw.line(points, fill=color, width=max(2, int((x2 - x1) / 90)), joint="curve")


def create_image(price, change, history):
    if not os.path.isfile(BASE_IMAGE):
        raise FileNotFoundError(f"عکس اصلی پیدا نشد: {BASE_IMAGE}")
    with Image.open(BASE_IMAGE) as source:
        image = source.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size
    white, green, red, muted = (255, 255, 255), (35, 230, 130), (255, 75, 90), (180, 190, 200)

    price_text = f"{chr(36)}{price:.5f}"
    price_font = fit_font(draw, price_text, int(width * 0.88), max(42, int(width * 0.105)), 20)
    draw.text(
        (width // 2, int(height * 0.48)), price_text, font=price_font,
        fill=white, anchor="mm", stroke_width=1, stroke_fill=(0, 0, 0)
    )

    left_x1, left_x2 = int(width * 0.10), int(width * 0.47)
    right_x1, right_x2 = int(width * 0.53), int(width * 0.90)
    value_font = get_font(max(18, int(width * 0.035)))
    dash_font = get_font(max(20, int(width * 0.04)))

    if change is None:
        for center in ((left_x1 + left_x2) // 2, (right_x1 + right_x2) // 2):
            draw.text((center, int(height * 0.79)), "—", font=dash_font, fill=muted, anchor="mm")
    elif change >= 0:
        draw.text(((left_x1 + left_x2) // 2, int(height * 0.77)), f"+{change:.2f}%", font=value_font, fill=green, anchor="mm")
        draw_chart(draw, history, left_x1, int(height * 0.82), left_x2, int(height * 0.90), green)
        draw.text(((right_x1 + right_x2) // 2, int(height * 0.79)), "—", font=dash_font, fill=muted, anchor="mm")
    else:
        draw.text(((left_x1 + left_x2) // 2, int(height * 0.79)), "—", font=dash_font, fill=muted, anchor="mm")
        draw.text(((right_x1 + right_x2) // 2, int(height * 0.80)), f"{change:.2f}%", font=value_font, fill=red, anchor="mm")
        draw_chart(draw, history, right_x1, int(height * 0.82), right_x2, int(height * 0.90), red)

    image.save(OUTPUT_IMAGE, "JPEG", quality=95, optimize=True)
    return OUTPUT_IMAGE


def get_iran_time():
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)
    return jalali.strftime("%Y/%m/%d"), now.strftime("%H:%M:%S")


def make_caption(price, change):
    date_text, time_text = get_iran_time()
    if change is None:
        status, change_text = "⚪ تغییر ۲۴ ساعته در دسترس نیست", "نامشخص"
    elif change > 0:
        status, change_text = "🟢 بازار امروز مثبته", f"+{change:.2f}%"
    elif change < 0:
        status, change_text = "🔴 بازار امروز منفیه", f"{change:.2f}%"
    else:
        status, change_text = "⚪ بازار بدون تغییره", "0.00%"

    return (
        "🧪 <b>گزارش لحظه‌ای قیمت LAB</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 <b>قیمت: {chr(36)}{price:.5f}</b>\n"
        f"📊 <b>تغییر ۲۴ ساعته: {change_text}</b>\n"
        f"{status}\n"
        "━━━━━━━━━━━━━━\n"
        f"📅 <b>تاریخ: {date_text}</b>\n"
        f"🕒 <b>ساعت ایران: {time_text}</b>\n"
        "🔄 <b>بروزرسانی خودکار هر ۵ دقیقه</b>\n\n"
        "👨‍💻 <b>طراح و سازنده: علی حامدی</b>\n"
        "👥 <b>گروه تلگرامی: @lab_chats</b>"
    )


def send_to_telegram(image_path, caption):
    if not BOT_TOKEN:
        raise ValueError("توکن ربات در GitHub Secrets تنظیم نشده است")
    if not CHAT_ID:
        raise ValueError("CHAT_ID خالی است")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
    with open(image_path, "rb") as photo:
        if MESSAGE_ID:
            media = {
                "type": "photo", "media": "attach://photo",
                "caption": caption, "parse_mode": "HTML"
            }
            response = HTTP.post(
                url + "editMessageMedia",
                data={
                    "chat_id": CHAT_ID, "message_id": MESSAGE_ID,
                    "media": json.dumps(media, ensure_ascii=False)
                },
                files={"photo": photo}, timeout=REQUEST_TIMEOUT
            )
        else:
            response = HTTP.post(
                url + "sendPhoto",
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"photo": photo}, timeout=REQUEST_TIMEOUT
            )

    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError("پاسخ تلگرام JSON معتبر نیست") from error
    if not result.get("ok"):
        raise RuntimeError("خطای تلگرام: " + str(result.get("description", "نامشخص")))

    message_id = (result.get("result") or {}).get("message_id")
    if not MESSAGE_ID and message_id:
        logging.warning(
            "پیام جدید ساخته شد؛ برای جلوگیری از پیام تکراری، MESSAGE_ID=%s را در GitHub Secrets ثبت کنید",
            message_id
        )
    logging.info("پیام LAB با موفقیت در تلگرام ثبت/ویرایش شد")


def main():
    logging.info("ربات قیمت LAB شروع به کار کرد")
    price, change = get_market_data()

    try:
        history = get_history()
    except Exception:
        logging.exception("تاریخچه دریافت نشد؛ تصویر بدون نمودار تازه ساخته می‌شود")
        history = []

    image_path = create_image(price, change, history)
    send_to_telegram(image_path, make_caption(price, change))
    logging.info("قیمت LAB: %.5f | تغییر ۲۴ ساعته: %s", price, "نامشخص" if change is None else f"{change:.2f}%")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("اجرای ربات ناموفق بود")
        raise
