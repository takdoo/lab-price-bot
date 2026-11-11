import os
import json
import logging
import math
import time
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
TELEGRAM_MAX_ATTEMPTS = 4
TELEGRAM_RETRY_SECONDS = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def build_session():
    retry = Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
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


def make_market_comment(change, history):
    """Informal pressure/trend read from price movement, not actual buyer counts."""
    recent_change = None
    if isinstance(history, list) and len(history) >= 4:
        try:
            earlier = float(history[max(0, int(len(history) * 0.75))])
            latest = float(history[-1])
            if earlier > 0 and math.isfinite(earlier) and math.isfinite(latest):
                recent_change = ((latest - earlier) / earlier) * 100
        except (TypeError, ValueError, IndexError):
            recent_change = None

    if change is None or recent_change is None:
        return "فعلاً از داده‌های موجود نمی‌شه فهمید کفه خرید سنگین‌تره یا فروش؛ برای پیش‌بینی هم عجله نکنیم."
    if recent_change >= 1.0 and change > 0:
        return "فعلاً فشار خرید بیشتره و حرکت کوتاه‌مدت رو به بالاست؛ شانس ادامه رشد هست، ولی تثبیت قیمت مهمه."
    if recent_change <= -1.0 and change < 0:
        return "فعلاً فشار فروش بیشتره و حرکت کوتاه‌مدت رو به پایینه؛ احتمال ادامه ضعف هست، مگر اینکه خریدارها وارد بشن."
    if recent_change >= 1.0 and change <= 0:
        return "توی چند ساعت اخیر خریدارها یه تکونی دادن، ولی جمع ۲۴ ساعت هنوز مثبته نشده؛ فعلاً برگشت قطعی نیست."
    if recent_change <= -1.0 and change >= 0:
        return "با اینکه جمع ۲۴ ساعت مثبته، توی حرکت اخیر فروشنده‌ها فعال‌تر شدن؛ مراقب ضعیف شدن روند باشیم."
    if change > 0:
        return "بازار یه مقدار مثبته، ولی زور خریدارها هنوز اون‌قدر واضح نیست که بگیم رشد ادامه‌داره."
    if change < 0:
        return "بازار یه مقدار منفیه، ولی فشار خرید و فروش نزدیک به همه؛ فعلاً جهت بعدی معلوم نیست."
    return "فعلاً بازار تقریباً بی‌حرکته؛ صبر کنیم تا کفه خرید یا فروش مشخص‌تر بشه."


def make_caption(price, change, history=None):
    date_text, time_text = get_iran_time()
    if change is None:
        status, change_text = "⚪ وضعیت بازار: نامشخص", "نامشخص"
    elif change > 0:
        status, change_text = "🟢 بازار ۲۴ ساعته مثبته", f"+{change:.2f}%"
    elif change < 0:
        status, change_text = "🔴 بازار ۲۴ ساعته منفیه", f"{change:.2f}%"
    else:
        status, change_text = "⚪ بازار بدون تغییره", "0.00%"

    comment = make_market_comment(change, history)
    return (
        "🧪 <b>گزارش لحظه‌ای LAB</b>\n"
        f"💰 <b>قیمت: {chr(36)}{price:.5f}</b>\n"
        f"📊 تغییر ۲۴ ساعته: <b>{change_text}</b>  |  {status}\n\n"
        "🗣️ <b>حرفِ بازار به زبون خودمونی:</b>\n"
        f"{comment}\n\n"
        f"📅 {date_text}  |  🕒 ساعت ایران {time_text}\n"
        "🔄 بروزرسانی خودکار هر ۵ دقیقه\n"
        "👨‍💻 طراح: علی حامدی  |  👥 @lab_chats"
    )


def send_to_telegram(image_path, caption):
    if not BOT_TOKEN:
        raise ValueError("توکن ربات در GitHub Secrets تنظیم نشده است")
    if not CHAT_ID:
        raise ValueError("CHAT_ID خالی است")
    if not MESSAGE_ID:
        raise ValueError("MESSAGE_ID در GitHub Secrets تنظیم نشده؛ شناسه پیام ثابت را ثبت کنید")
    if not MESSAGE_ID.isdigit():
        raise ValueError("MESSAGE_ID باید فقط شامل عدد باشد")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageMedia"
    media = {
        "type": "photo", "media": "attach://photo",
        "caption": caption, "parse_mode": "HTML"
    }
    last_error = None

    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            # Reopen the stream on each attempt; requests consumes file handles.
            with open(image_path, "rb") as photo:
                response = HTTP.post(
                    url,
                    data={
                        "chat_id": CHAT_ID,
                        "message_id": int(MESSAGE_ID),
                        "media": json.dumps(media, ensure_ascii=False)
                    },
                    files={"photo": photo},
                    timeout=REQUEST_TIMEOUT
                )

            try:
                result = response.json()
            except ValueError as error:
                response.raise_for_status()
                raise RuntimeError("پاسخ تلگرام JSON معتبر نیست") from error

            if result.get("ok"):
                logging.info("پیام ثابت LAB با موفقیت ویرایش شد؛ message_id=%s", MESSAGE_ID)
                return

            description = str(result.get("description", "خطای نامشخص تلگرام"))
            if "message is not modified" in description.lower():
                logging.info("پیام تلگرام از قبل همین محتوا را دارد؛ خطای واقعی نیست")
                return

            error_code = int(result.get("error_code", response.status_code))
            last_error = RuntimeError(f"خطای تلگرام {error_code}: {description}")
            if error_code == 429 or error_code >= 500:
                retry_after = (result.get("parameters") or {}).get("retry_after", TELEGRAM_RETRY_SECONDS)
                if attempt < TELEGRAM_MAX_ATTEMPTS:
                    delay = min(max(int(retry_after), 1), 60)
                    logging.warning("خطای موقت تلگرام؛ تلاش %s از %s پس از %s ثانیه", attempt + 1, TELEGRAM_MAX_ATTEMPTS, delay)
                    time.sleep(delay)
                    continue
            raise last_error

        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt < TELEGRAM_MAX_ATTEMPTS:
                delay = TELEGRAM_RETRY_SECONDS * attempt
                logging.warning("ارتباط با تلگرام قطع شد؛ تلاش مجدد در %s ثانیه (%s/%s)", delay, attempt + 1, TELEGRAM_MAX_ATTEMPTS)
                time.sleep(delay)
                continue
            break

    raise RuntimeError(f"ویرایش پیام تلگرام پس از {TELEGRAM_MAX_ATTEMPTS} تلاش ناموفق بود: {last_error}")


def main():
    logging.info("ربات قیمت LAB شروع به کار کرد")
    price, change = get_market_data()

    try:
        history = get_history()
    except Exception:
        logging.exception("تاریخچه دریافت نشد؛ تصویر بدون نمودار تازه ساخته می‌شود")
        history = []

    image_path = create_image(price, change, history)
    send_to_telegram(image_path, make_caption(price, change, history))
    logging.info("قیمت LAB: %.5f | تغییر ۲۴ ساعته: %s", price, "نامشخص" if change is None else f"{change:.2f}%")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("اجرای ربات ناموفق بود")
        raise
